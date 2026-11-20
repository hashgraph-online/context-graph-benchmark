"""Regression for a real artifact-publication defect, exercised by the repair loop."""

from pathlib import Path
import tempfile
import unittest

from cgb.evidence import seal
from cgb.jsonio import Invalid


class SealRootGuard(unittest.TestCase):
    def test_rejected_symlink_root_does_not_publish_into_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root/'original'
            target.mkdir()
            alias = root/'alias'
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaises(Invalid):
                seal(alias, 'run')
            self.assertFalse((target/'manifest.json').exists(),
                             'Reject a symlink output root before writing any manifest into its target.')
