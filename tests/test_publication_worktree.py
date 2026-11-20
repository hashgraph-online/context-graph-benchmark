"""Git worktrees must retain the same publication inventory protections."""

from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('publication_check', Path(__file__).resolve().parents[1] / 'scripts/check_publication.py')
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


class PublicationWorktree(unittest.TestCase):
    def test_linked_worktree_uses_git_inventory_and_rejects_tracked_private_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, worktree, hooks = root / 'repository', root / 'worktree', root / 'hooks'
            repository.mkdir()
            hooks.mkdir()
            def git(cwd, *arguments):
                return subprocess.run(['git', '-c', 'init.templateDir=', '-c', 'core.hooksPath=' + str(hooks),
                    '-c', 'user.name=Publication fixture', '-c', 'user.email=fixture@example.invalid', *arguments],
                    cwd=cwd, capture_output=True, text=True, check=True,
                    env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull})
            git(repository, 'init')
            (repository / 'README.md').write_text('Public fixture.\n')
            (repository / '.gitignore').write_text('.cgb/\n')
            git(repository, 'add', 'README.md', '.gitignore')
            git(repository, 'commit', '-m', 'Public fixture')
            git(repository, 'worktree', 'add', '-b', 'fixture-worktree', str(worktree))
            self.assertTrue((worktree / '.git').is_file())
            with patch.object(publication, 'ROOT', worktree), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
                self.assertNotIn(Path('.git'), publication.candidates())
                self.assertEqual(publication.main(), 0)
                (worktree / '.cgb').mkdir()
                (worktree / '.cgb' / 'fixture.txt').write_text('Private-path tracking fixture; no credentials.\n')
                git(worktree, 'add', '-f', '.cgb/fixture.txt')
                self.assertEqual(publication.main(), 1)
                self.assertIn('private/generated directory tracked', error.getvalue())

    def test_invalid_git_marker_fails_instead_of_falling_back_to_export_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').write_text('invalid Git metadata fixture\n')
            (root / 'README.md').write_text('Public fixture.\n')
            with patch.object(publication, 'ROOT', root), self.assertRaises(subprocess.CalledProcessError):
                publication.candidates()
