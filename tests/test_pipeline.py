from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from cgb.evidence import verify
from cgb.jsonio import Invalid, read_json
from cgb.runner import Channel, make_plan, run
from cgb.scoring import export, replay, score

ROOT=Path(__file__).resolve().parents[1]


class Protocol(unittest.TestCase):
    def test_deadline_and_partial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            channel=Channel([sys.executable,'-c','import sys,time;sys.stdout.write("partial");sys.stdout.flush();time.sleep(10)'],temporary,ROOT/'.cgb/tokenizer',100)
            try:
                with self.assertRaises(TimeoutError): channel.call('describe',{})
            finally:
                self.assertFalse(channel.close())
            self.assertEqual((Path(temporary)/'stdout.bin').read_bytes(),b'partial')

    def test_input_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            channel=Channel([sys.executable,'-c','import time;time.sleep(10)'],temporary,ROOT/'.cgb/tokenizer',100)
            try:
                with self.assertRaises(TimeoutError):
                    document={'id':'d','version':'r1','text':'a'*1000000,'source':'d','project':'p','valid_from':None,'valid_to':None}
                    channel.call('ingest',{'documents':[document],'edges':[]})
            finally:
                channel.close()
            self.assertEqual(channel.phases[0]['status'],'timeout')

    def test_wrong_response_identity(self):
        program='import sys,json;sys.stdin.readline();print(json.dumps({"schema":"cgb.adapter-response.v1","id":"wrong","status":"error","payload":{},"error":"failure"}),flush=True)'
        with tempfile.TemporaryDirectory() as temporary:
            channel=Channel([sys.executable,'-c',program],temporary,ROOT/'.cgb/tokenizer',1000)
            try:
                with self.assertRaises(Invalid): channel.call('describe',{})
            finally:
                self.assertFalse(channel.close())

    def test_wrong_success_payload_for_operation(self):
        program='import sys,json;request=json.loads(sys.stdin.readline());print(json.dumps({"schema":"cgb.adapter-response.v1","id":request["id"],"status":"ok","payload":{"ready":True,"revision":"r1"},"error":None}),flush=True)'
        with tempfile.TemporaryDirectory() as temporary:
            channel=Channel([sys.executable,'-c',program],temporary,ROOT/'.cgb/tokenizer',1000)
            try:
                with self.assertRaises(Invalid): channel.call('describe',{})
            finally:
                self.assertFalse(channel.close())

    def test_child_death_and_malformed_output(self):
        for program in ['raise SystemExit(23)', 'print("{broken",flush=True)']:
            with self.subTest(program=program), tempfile.TemporaryDirectory() as temporary:
                channel=Channel([sys.executable,'-c',program],temporary,ROOT/'.cgb/tokenizer',1000)
                try:
                    with self.assertRaises(Invalid): channel.call('describe',{})
                finally:
                    self.assertFalse(channel.close())

    def test_closed_stdin_is_a_retained_protocol_failure_and_child_is_reaped(self):
        program='import os,sys,time;from pathlib import Path;os.close(0);Path(sys.argv[1]).write_text("closed");time.sleep(10)'
        with tempfile.TemporaryDirectory() as temporary:
            marker=Path(temporary)/'stdin-closed'
            channel=Channel([sys.executable,'-c',program,str(marker)],temporary,ROOT/'.cgb/tokenizer',1000)
            try:
                deadline=time.monotonic()+5
                while not marker.exists() and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertTrue(marker.exists(),'child did not signal that stdin was closed')
                with self.assertRaisesRegex(Invalid,'adapter-input-ended'):
                    channel.call('describe',{})
            finally:
                self.assertFalse(channel.close())
            self.assertIsNotNone(channel.process.returncode)
            self.assertEqual(channel.phases[0]['status'],'error')
            self.assertIn(b'"direction":"request"',(Path(temporary)/'journal.jsonl').read_bytes())

    def test_extra_output_is_cleanup_failure(self):
        program='import sys,json;request=json.loads(sys.stdin.readline());print(json.dumps({"schema":"cgb.adapter-response.v1","id":request["id"],"status":"error","payload":{},"error":"failure"}),flush=True);print("extra",flush=True)'
        with tempfile.TemporaryDirectory() as temporary:
            channel=Channel([sys.executable,'-c',program],temporary,ROOT/'.cgb/tokenizer',1000)
            try:
                channel.call('describe',{})
                channel.close_ack=True
            finally:
                self.assertFalse(channel.close())

    def test_flood_is_bounded(self):
        program='import sys;sys.stdout.buffer.write(b"x"*(9*1024*1024));sys.stdout.flush()'
        with tempfile.TemporaryDirectory() as temporary:
            channel=Channel([sys.executable,'-c',program],temporary,ROOT/'.cgb/tokenizer',1000)
            try:
                with self.assertRaises(Invalid): channel.call('describe',{})
            finally:
                self.assertFalse(channel.close())
            self.assertLess((Path(temporary)/'stdout.bin').stat().st_size,10*1024*1024)

    def test_unacknowledged_readiness_and_failed_cleanup(self):
        program='import sys,json,time;request=json.loads(sys.stdin.readline());print(json.dumps({"schema":"cgb.adapter-response.v1","id":request["id"],"status":"ok","payload":{"ready":False,"revision":"r1"},"error":None}),flush=True);time.sleep(10)'
        with tempfile.TemporaryDirectory() as temporary:
            channel=Channel([sys.executable,'-c',program],temporary,ROOT/'.cgb/tokenizer',1000)
            try:
                _,response=channel.call('reset',{'namespace':'n'})
                self.assertFalse(response['payload']['ready'])
                channel.close_ack=True
            finally:
                self.assertFalse(channel.close())


class Pipeline(unittest.TestCase):
    def test_interruption_retains_partial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            plan=make_plan(ROOT/'suites/smoke-v1.json',[ROOT/'adapters/empty.json'],ROOT/'.cgb/tokenizer',budgets=[512])
            with patch('cgb.runner.Channel.call',side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt): run(plan,root/'run',ROOT/'.cgb/tokenizer')
            self.assertEqual(verify(root/'run')['disposition'],'partial')
            self.assertEqual(len((root/'run/attempts.jsonl').read_bytes().splitlines()),1)
            with self.assertRaises(Invalid): score(root/'run',ROOT/'datasets/synthetic/smoke-labels.json',root/'report')

    def test_real_baselines_replay_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            plan=make_plan(ROOT/'suites/smoke-v1.json',[ROOT/'adapters/empty.json',ROOT/'adapters/bm25.json'],ROOT/'.cgb/tokenizer',seed=7,budgets=[512])
            result=run(plan,root/'run',ROOT/'.cgb/tokenizer')
            self.assertEqual(result['attempts'],8)
            score(root/'run',ROOT/'datasets/synthetic/smoke-labels.json',root/'report')
            raw=read_json(root/'report/result.json')
            self.assertEqual(raw['adapters']['empty']['512']['evidence_score'],0)
            self.assertGreater(raw['adapters']['bm25']['512']['evidence_score'],0)
            self.assertEqual(raw['adapters']['bm25']['512']['statuses'],{'ok':4})
            self.assertEqual(replay(root/'run',root/'report')['replay'],'passed')
            export(root/'run',root/'report',root/'export.zip')
            (root/'run/attempts.jsonl').write_bytes(b'')
            with self.assertRaises(Invalid): replay(root/'run',root/'report')


if __name__=='__main__':unittest.main()
