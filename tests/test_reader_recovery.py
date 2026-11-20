"""Real-child regression cases for failed, interrupted and stale model exchanges."""

import json
import os
from pathlib import Path
import tempfile
import subprocess
import threading
import unittest
from unittest.mock import patch
import sys

from cgb.jsonio import Invalid
from cgb.local_models import MlxClient


WORKER = '''import json,sys,time
from pathlib import Path
print(json.dumps({"ready":True}),flush=True)
for line in sys.stdin:
 request=json.loads(line)
 with (Path(sys.argv[1])/"dispatched.jsonl").open("a") as log:
  log.write(json.dumps({"id":request["id"]})+"\\n")
 if request.get("mode")=="late": time.sleep(30)
 if request.get("mode") in ("slow","slow-unmetered","slow-wrong"): time.sleep(.3)
 if request.get("mode")=="slow-unmetered":
  print(json.dumps({"id":request["id"],"status":"ok","input_tokens":3,"output_tokens":None}),flush=True)
  continue
 if request.get("mode")=="slow-wrong":
  print(json.dumps({"id":"other","status":"ok","input_tokens":3,"output_tokens":1}),flush=True)
  continue
 if request.get("mode")=="wrong":
  print(json.dumps({"id":"unsolicited"}),flush=True)
 elif request.get("mode")=="malformed":
  print("[1,2,3]",flush=True)
 else:
  print(json.dumps({"id":request["id"],"status":"ok","text":"fixture",
   "input_tokens":3,"output_tokens":1}),flush=True)
'''


class ReaderRecovery(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        (root / 'local_mlx_worker.py').write_text(WORKER)
        owned = root / 'owned'
        owned.mkdir()
        return root, owned

    def test_late_usage_is_charged_once_without_answer_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            root, owned = self.fixture(directory)
            with patch('cgb.local_models.__file__', str(root / 'client.py')):
                client = MlxClient({'python': sys.executable, 'model_path': str(root)}, owned, 3)
                try:
                    first = client.process
                    result = client.exchange({'id': 'primary', 'mode': 'slow'}, timeout=.1, usage_grace=2)
                    self.assertEqual((result['status'], result['text'], result['finish_reason']), ('error', '', 'deadline'))
                    self.assertEqual((result['input_tokens'], result['output_tokens']), (3, 1))
                    self.assertEqual(result['usage_source'], 'late-native-completion')
                    self.assertGreater(result['seconds_past_deadline'], 0)
                    self.assertIsNotNone(first.poll())
                    next_result = client.exchange({'id': 'next', 'mode': 'slow'}, timeout=2, usage_grace=1)
                    self.assertEqual((next_result['status'], next_result['text']), ('ok', 'fixture'))
                    self.assertEqual(client.timeout, 3)
                finally:
                    client.close()
                events = [json.loads(line) for line in (owned / 'worker-lifecycle.jsonl').read_text().splitlines()]
                failures = [e for e in events if e['event'] == 'exchange-failed']
                self.assertEqual(len(failures), 1)
                self.assertTrue(failures[0]['usage_recorded'])
                calls = [json.loads(line)['id'] for line in (root / 'dispatched.jsonl').read_text().splitlines()]
                self.assertEqual(calls, ['primary', 'next'])

    def test_cost_receipt_must_be_complete_correlated_and_within_grace(self):
        for mode, error in [('slow-unmetered', TimeoutError), ('slow-wrong', Invalid), ('late', TimeoutError)]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root, owned = self.fixture(directory)
                with patch('cgb.local_models.__file__', str(root / 'client.py')):
                    client = MlxClient({'python': sys.executable, 'model_path': str(root)}, owned, 3)
                    try:
                        with self.assertRaises(error):
                            client.exchange({'id': 'primary', 'mode': mode}, timeout=.1, usage_grace=.7)
                        self.assertIsNotNone(client.process.poll())
                    finally:
                        client.close()

    def test_timeout_reaps_before_next_registered_request_and_never_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            root, owned = self.fixture(directory)
            with patch('cgb.local_models.__file__', str(root / 'client.py')):
                client = MlxClient({'python': sys.executable, 'model_path': str(root)}, owned, 3)
                try:
                    first = client.process
                    client.timeout = .1
                    with self.assertRaisesRegex(TimeoutError, 'local model deadline'):
                        client.exchange({'id': 'failed-primary', 'mode': 'late'})
                    self.assertIsNotNone(first.poll(), 'failed exchange must reap its worker before returning')
                    client.timeout = 3
                    response = client.exchange({'id': 'next-registered-primary'})
                    self.assertEqual(response['id'], 'next-registered-primary')
                    self.assertEqual((response['input_tokens'], response['output_tokens']), (3, 1))
                    self.assertNotEqual(client.process.pid, first.pid)
                    dispatched = [json.loads(line)['id'] for line in (root / 'dispatched.jsonl').read_text().splitlines()]
                    self.assertEqual(dispatched, ['failed-primary', 'next-registered-primary'])
                finally:
                    client.close()
                self.assertIsNotNone(client.process.poll())
                events = [json.loads(line) for line in (owned / 'worker-lifecycle.jsonl').read_text().splitlines()]
                self.assertEqual([e['event'] for e in events].count('exchange-failed'), 1)
                self.assertEqual([e['event'] for e in events].count('worker-ready'), 2)

    def test_wrong_identity_and_nonobject_frames_close_before_recovery(self):
        for mode in ('wrong', 'malformed'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root, owned = self.fixture(directory)
                with patch('cgb.local_models.__file__', str(root / 'client.py')):
                    client = MlxClient({'python': sys.executable, 'model_path': str(root)}, owned, 3)
                    try:
                        first = client.process
                        with self.assertRaises(Invalid):
                            client.exchange({'id': 'bad-primary', 'mode': mode})
                        self.assertIsNotNone(first.poll())
                        self.assertEqual(client.exchange({'id': 'next-primary'})['id'], 'next-primary')
                        self.assertNotEqual(first.pid, client.process.pid)
                    finally:
                        client.close()

    def test_interruption_retains_no_reusable_process_or_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            root, owned = self.fixture(directory)
            with patch('cgb.local_models.__file__', str(root / 'client.py')):
                client = MlxClient({'python': sys.executable, 'model_path': str(root)}, owned, 3)
                try:
                    first = client.process
                    with patch.object(client.selector, 'select', side_effect=KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            client.exchange({'id': 'interrupted-primary'})
                    self.assertIsNotNone(first.poll())
                    self.assertEqual(client.exchange({'id': 'next-primary'})['id'], 'next-primary')
                finally:
                    client.close()

    def test_explicit_close_is_terminal_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root, owned = self.fixture(directory)
            with patch('cgb.local_models.__file__', str(root / 'client.py')):
                client = MlxClient({'python': sys.executable, 'model_path': str(root)}, owned, 3)
                client.close()
                client.close()
                with self.assertRaisesRegex(Invalid, 'closed'):
                    client.exchange({'id': 'must-not-dispatch'})
                self.assertFalse((root / 'dispatched.jsonl').exists())

    def test_repeated_recovery_closes_retired_pipes_and_preserves_output_accounting(self):
        with tempfile.TemporaryDirectory() as directory:
            root, owned = self.fixture(directory)
            before = len(os.listdir('/dev/fd')) if Path('/dev/fd').is_dir() else None
            with patch('cgb.local_models.__file__', str(root / 'client.py')):
                client = MlxClient({'python': sys.executable, 'model_path': str(root)}, owned, 3)
                processes, last_total = [], client.total
                try:
                    for number in range(8):
                        process = client.process
                        processes.append(process)
                        with self.assertRaises(Invalid):
                            client.exchange({'id': 'bad-' + str(number), 'mode': 'wrong'})
                        self.assertIsNotNone(process.poll())
                        self.assertTrue(all(s.closed for s in (process.stdin, process.stdout, process.stderr)))
                        response = client.exchange({'id': 'next-' + str(number)})
                        self.assertEqual(response['id'], 'next-' + str(number))
                        self.assertGreater(client.total, last_total)
                        last_total = client.total
                finally:
                    client.close()
                self.assertTrue(client.log.closed and client.lifecycle.closed)
                self.assertTrue(all(p.poll() is not None for p in processes))
            if before is not None:
                self.assertEqual(len(os.listdir('/dev/fd')), before)

    def test_close_waits_for_an_inflight_recovery_and_leaves_no_new_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root, owned = self.fixture(directory)
            with patch('cgb.local_models.__file__', str(root / 'client.py')):
                client = MlxClient({'python': sys.executable, 'model_path': str(root)}, owned, 5)
                with self.assertRaises(Invalid):
                    client.exchange({'id': 'bad-primary', 'mode': 'wrong'})
                entered, release, close_started, close_finished = (threading.Event() for _ in range(4))
                errors = []
                real_popen = subprocess.Popen
                def launch(*args, **kwargs):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('recovery fixture gate timed out')
                    return real_popen(*args, **kwargs)
                def exchange():
                    try:
                        client.exchange({'id': 'cancelled-recovery-primary'})
                    except BaseException as error:
                        errors.append(error)
                def close():
                    close_started.set()
                    try:
                        client.close()
                    except BaseException as error:
                        errors.append(error)
                    finally:
                        close_finished.set()
                worker = threading.Thread(target=exchange)
                closer = threading.Thread(target=close)
                try:
                    with patch('cgb.local_models.subprocess.Popen', side_effect=launch):
                        worker.start()
                        self.assertTrue(entered.wait(5))
                        closer.start()
                        self.assertTrue(close_started.wait(5))
                        self.assertFalse(close_finished.wait(.2), 'close must retain ownership of pending startup')
                        release.set()
                        worker.join(8)
                        closer.join(8)
                    self.assertFalse(worker.is_alive() or closer.is_alive())
                    self.assertTrue(close_finished.is_set())
                    self.assertEqual(len(errors), 1)
                    self.assertIsInstance(errors[0], Invalid)
                    self.assertIsNotNone(client.process.poll())
                    dispatched = [json.loads(line)['id'] for line in (root / 'dispatched.jsonl').read_text().splitlines()]
                    self.assertEqual(dispatched, ['bad-primary'])
                finally:
                    release.set()
                    if worker.ident is not None:
                        worker.join(8)
                    if closer.ident is not None:
                        closer.join(8)
                    client.close()
