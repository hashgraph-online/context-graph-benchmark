from pathlib import Path
import sys
import tempfile
import unittest

from cgb.workspace import command, workspace_lock
from cgb.jsonio import Invalid


class Commands(unittest.TestCase):
    def test_real_deadline_retains_logs_and_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=command([sys.executable,'-c','import time;print("started",flush=True);time.sleep(10)'],
                           temporary,Path(temporary)/'command',.25)
            self.assertEqual(result['status'],'timeout')
            self.assertIsNotNone(result['returncode'])
            self.assertIn(b'started',(Path(temporary)/'command/stdout.log').read_bytes())

    def test_missing_executable_is_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=command([str(Path(temporary)/'does-not-exist')],temporary,Path(temporary)/'command',1)
            self.assertEqual(result['status'],'launch_error')
            self.assertIsNone(result['returncode'])


    def test_workspace_lock_is_exclusive_and_reusable(self):
        with tempfile.TemporaryDirectory() as root:
            with workspace_lock(root):
                with self.assertRaises(Invalid), workspace_lock(root):
                    pass
            with workspace_lock(root):
                pass
