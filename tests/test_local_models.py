import copy
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from cgb.jsonio import Invalid, digest, encoded, read_json, write_json
from cgb.local_models import MlxClient, bind_mlx, endpoint, http_call, http_json, validate_reader, verify_artifacts
from cgb.reader_protocol import grade


def fixture_reader(base_url="http://127.0.0.1:1234/v1"):
    reader = {"schema": "cgb.local-reader.v1", "id": "http-fixture", "backend": "openai-compatible",
              "model": "test-only-not-real-model", "revision": "test-only", "base_url": base_url,
              "context_window": 4096, "settings": {"temperature": 0, "top_p": 1, "max_tokens": 32,
              "enable_thinking": False}, "artifacts": []}
    return reader


class LocalModels(unittest.TestCase):
    def test_worker_uses_stdlib_platform_despite_adjacent_name_and_closes_on_eof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'platform.py').write_text("raise AssertionError('adjacent module must never shadow the standard library')\n")
            (root/'local_mlx_worker.py').write_text("import platform,json,sys\nprint(json.dumps({'ready':True,'implementation':platform.python_implementation()}),flush=True)\nfor line in sys.stdin:\n request=json.loads(line)\n print(json.dumps({'id':request['id'],'status':'ok','text':platform.system()}),flush=True)\n")
            owned=root/'owned';owned.mkdir()
            with patch('cgb.local_models.__file__',str(root/'client.py')):
                client=MlxClient({'python':sys.executable,'model_path':str(root)},owned,5)
                self.assertEqual(client.ready['implementation'],'CPython')
                self.assertEqual(client.exchange({'id':'fixture'})['status'],'ok')
                client.close()
                self.assertEqual(client.process.returncode,0)

    def test_dead_worker_startup_error_is_not_masked_by_group_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'local_mlx_worker.py').write_text("import sys\nprint('explicit startup failure fixture',file=sys.stderr,flush=True)\nraise SystemExit(7)\n")
            owned=root/'owned';owned.mkdir()
            with patch('cgb.local_models.__file__',str(root/'client.py')):
                with self.assertRaisesRegex(Invalid,'local worker ended'):
                    MlxClient({'python':sys.executable,'model_path':str(root)},owned,5)
            self.assertIn('explicit startup failure fixture',(owned/'worker-stderr.log').read_text())

    def test_loopback_only_no_credentials_proxy_or_host_aliases(self):
        self.assertEqual(endpoint("http://localhost:1234/v1"), "http://127.0.0.1:1234/v1")
        for url in ("https://example.com/v1", "http://10.0.0.1:1234", "file:///tmp/model", "http://user:secret@127.0.0.1:1234", "http://127.0.0.1?key=x"):
            with self.subTest(url=url), self.assertRaises(Invalid):
                endpoint(url)

    def test_mounted_binding_and_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config.json", {"max_position_embeddings": 4096})
            (root / "model.safetensors").write_bytes(b"hash-binding fixture; never loaded as a model")
            bind_mlx(root, sys.executable, root / "reader.json", "test", "test", "test")
            reader = read_json(root / "reader.json")
            verify_artifacts(reader)
            (root / "model.safetensors").write_bytes(b"changed")
            with self.assertRaises(Invalid):
                verify_artifacts(reader)

    def test_decoding_limits_and_missing_model_identity(self):
        reader = fixture_reader()
        for key, value in (("max_tokens", True), ("max_tokens", 0), ("temperature", -1), ("top_p", 2)):
            broken = copy.deepcopy(reader)
            broken["settings"][key] = value
            with self.subTest(key=key), self.assertRaises(Invalid):
                validate_reader(broken)

    def test_entire_display_and_typed_grading(self):
        target = {"expected_status": "completed", "state": {"count": 1}}
        for text in ('{"count":true}', '{"count":1,"extra":"claim"}', '{"count":1} trailing claim', '```json\n{"count":1}\n```', '{"count":1,"count":1}'):
            with self.subTest(text=text):
                self.assertFalse(grade(text, {"count":"integer"}, target, "ok")[3])
        self.assertTrue(grade('{"count":1}', {"count":"integer"}, target, "ok")[3])
        self.assertFalse(grade('{"count":1}', {"count":"integer"}, target, "error")[3])

    def test_unknown_task_needs_actual_successful_abstention(self):
        target = {"expected_status": "abstained", "state": {}}
        for text, status in (("", "ok"), ("{}", "timeout"), ('{"owner":"guess"}', "ok")):
            self.assertFalse(grade(text, {"owner":"string"}, target, status)[3])
        self.assertTrue(grade("{}", {"owner":"string"}, target, "ok")[3])

    def test_model_substitution_and_http_redirect_rejected(self):
        reader = fixture_reader()
        request = {'settings':reader['settings'], 'messages':[], 'seed':0, 'id':'test'}
        with patch('cgb.local_models.http_json', return_value={'model':'different','choices':[]}):
            with self.assertRaises(Invalid): http_call(reader, request, 1)

    def test_real_local_http_redirect_never_leaves_loopback(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location','https://example.com/never-follow')
                self.end_headers()
            def log_message(self,*args):
                pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with self.assertRaisesRegex(Invalid,'redirects are disabled'):
                http_json(f'http://127.0.0.1:{server.server_port}/v1','/models',timeout=1)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_actual_worker_deadline_reaps_process_and_rejects_wrong_identity(self):
        real_popen=subprocess.Popen
        for mode in ('timeout','wrong-id'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                fixture=root/'fixture.py'
                fixture.write_text('import json,sys,time\nprint(json.dumps({"ready":True}),flush=True)\nfor line in sys.stdin:\n'
                    + (' time.sleep(30)\n' if mode=='timeout' else ' print(json.dumps({"id":"wrong"}),flush=True)\n'))
                def launch(argv,**kwargs):
                    return real_popen([sys.executable,'-u',str(fixture)],**kwargs)
                with patch('cgb.local_models.subprocess.Popen',side_effect=launch):
                    client=MlxClient({'python':sys.executable,'model_path':str(root)},root,1)
                try:
                    client.timeout=.05 if mode=='timeout' else 1
                    with self.assertRaises(TimeoutError if mode=='timeout' else Invalid):
                        client.exchange({'id':'registered-request'})
                finally:
                    client.close()
                self.assertIsNotNone(client.process.poll())

    def test_worker_initialization_failure_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = {'python':'/nonexistent/python', 'model_path':directory}
            with self.assertRaises(FileNotFoundError):
                MlxClient(reader, directory, 1)
