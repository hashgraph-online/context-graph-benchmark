"""Persistent generic adapter mutations, scope/handoff and physical restart."""

from pathlib import Path
import sys
import tempfile
import unittest

from cgb.portable_soak import CompressedChannel, checked_context, phase, task_payload, verify_bound_inputs
from cgb.evidence import file_hash, implementation_identity
from cgb.jsonio import Invalid
from cgb.tokenization import Counter


class PersistentLifecycle(unittest.TestCase):
    def test_runtime_drift_fails_before_a_duration_result_can_be_awarded(self):
        from unittest.mock import patch
        counter = Counter(Path(__file__).resolve().parents[1] / '.cgb/tokenizer')
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / 'explicit-engine-fixture'
            artifact.write_bytes(b'original fixture bytes')
            sha, size = file_hash(artifact)
            plan = {'evaluator_sha256': implementation_identity(), 'tokenizer': counter.identity,
                    'adapters': {'fixture': {'artifacts': [{'path': str(artifact), 'sha256': sha, 'bytes': size}]}},
                    'config': {'reader': None}}
            verify_bound_inputs(plan, counter, reader=True)
            artifact.write_bytes(b'changed fixture bytes')
            with self.assertRaisesRegex(Invalid, 'bound engine artifact changed'):
                verify_bound_inputs(plan, counter)
            artifact.write_bytes(b'original fixture bytes')
            with patch('cgb.portable_soak.implementation_identity', return_value='0' * 64):
                with self.assertRaisesRegex(Invalid, 'evaluator changed'):
                    verify_bound_inputs(plan, counter)

    def test_changed_facts_scope_refusal_handoff_and_actual_process_restart(self):
        tokenizer = Path(__file__).resolve().parents[1] / '.cgb/tokenizer'
        counter = Counter(tokenizer)
        with tempfile.TemporaryDirectory() as directory:
            def open_channel(name):
                work = Path(directory) / name
                work.mkdir()
                channel = CompressedChannel([sys.executable, '-m', 'cgb.adapters', '--engine', 'bm25'], work, tokenizer, 5000)
                channel.call('describe', {})
                channel.call('reset', {'namespace': 'agent-0'})
                return channel
            def ingest_query(channel, number):
                source, public, _ = phase(number)
                channel.call('ingest', {'documents': public['documents'], 'edges': public['edges']})
                _, response = channel.call('query', task_payload(source, 0, 'agent-0'))
                self.assertTrue(checked_context(source, public, 0, response, counter)['correct'])
                return source, public, response
            def close(channel):
                _, acknowledgement = channel.call('close', {'namespace': 'agent-0'})
                channel.close_ack = acknowledgement['status'] == 'ok'
                self.assertTrue(channel.close())
            channel = open_channel('first')
            first_pid = channel.process.pid
            try:
                _, _, original = ingest_query(channel, 0)
                source, public, changed = ingest_query(channel, 1)
                self.assertNotEqual(original['payload']['blocks'], changed['payload']['blocks'])
                self.assertTrue(all(c['version'] == 'p1' for b in changed['payload']['blocks'] for c in b['citations']))
                channel.call('set_scope', {'documents': [], 'ids': [], 'scope': {'project': None, 'allowed_ids': ['policy']}})
                _, denied = channel.call('query', task_payload(source, 0, 'agent-0'))
                self.assertEqual(denied['status'], 'refused')
                channel.call('handoff', {'documents': [], 'ids': [], 'scope': task_payload(source, 0, 'agent-0')['scope']})
                _, restored = channel.call('query', task_payload(source, 0, 'agent-0'))
                self.assertTrue(checked_context(source, public, 0, restored, counter)['correct'])
            finally:
                close(channel)
            restarted = open_channel('restarted')
            try:
                self.assertNotEqual(restarted.process.pid, first_pid)
                _, _, recovered = ingest_query(restarted, 1)
                self.assertEqual(recovered['payload']['blocks'], changed['payload']['blocks'])
            finally:
                close(restarted)
