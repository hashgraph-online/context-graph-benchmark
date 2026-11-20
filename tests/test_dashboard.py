"""Actual local HTTP/job contracts; no simulated benchmark observations."""

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from cgb.dashboard import handler
from cgb.workspace import initialize
from cgb.dashboard_control import DashboardController
from cgb.jsonio import read_json, write_json


class LiveDashboard(unittest.TestCase):
    def test_private_oracles_and_work_files_are_denied_even_under_old_result_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in ("lab/assurance/example/private/oracles.json", "lab/portable-soaks/example/private/oracles.json", "reports/example/submission/private/oracles.json", "lab/portable-soaks/example/work/native.txt"):
                target = root/path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic-value-that-must-not-be-served")
            write_json(root/"lab/assurance/example/result.json", {"public": True})
            server = ThreadingHTTPServer(("127.0.0.1",0), handler(root))
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            connection = HTTPConnection("127.0.0.1",server.server_address[1],timeout=10)
            try:
                for path in ("lab/assurance/example/private/oracles.json", "lab/portable-soaks/example/private/oracles.json", "lab/portable-soaks/example/PRIVATE/oracles.json", "reports/example/submission/private/oracles.json", "lab/portable-soaks/example/work/native.txt"):
                    connection.request("GET","/api/artifact?path="+path)
                    response = connection.getresponse()
                    self.assertEqual(response.status,400)
                    self.assertNotIn(b"synthetic-value",response.read())
                connection.request("GET","/api/artifact?path=lab/assurance/example/result.json")
                response = connection.getresponse()
                self.assertEqual(response.status,200)
                self.assertEqual(json.loads(response.read()), {"public":True})
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(5)

    def test_controls_require_local_origin_and_token_and_stop_actual_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            initialize(root)
            import shutil
            from cgb.platform import register
            tokenizer = Path(__file__).resolve().parents[1] / ".cgb/tokenizer"
            shutil.copytree(tokenizer, root / ".cgb/tokenizer")
            register(root, "reference", root / "adapters/bm25.json")
            controller = DashboardController(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(root, controller))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host = f"127.0.0.1:{server.server_port}"
            def post(route, value, token):
                connection = HTTPConnection(host, timeout=5)
                try:
                    connection.request("POST", route, json.dumps(value), {
                        "Content-Type": "application/json", "Origin": "http://" + host,
                        "X-CGB-Control-Token": token})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()
            try:
                value = {"kind": "platform-compare", "engines": ["reference"]}
                self.assertEqual(post("/api/jobs/start", value, "invalid")[0], 400)
                status, job = post("/api/jobs/start", value, controller.token)
                self.assertEqual(status, 202)
                self.assertEqual(post("/api/jobs/start", value, controller.token)[0], 400)
                deadline = time.monotonic() + 15
                while not (root / job["path"] / "command/stdout.log").exists() and time.monotonic() < deadline:
                    time.sleep(.025)
                self.assertEqual(post("/api/jobs/stop", {"id": job["id"]}, controller.token)[0], 202)
                while controller.status()["active_job"] and time.monotonic() < deadline:
                    time.sleep(.025)
                self.assertEqual(read_json(root / job["path"] / "job.json")["status"], "stopped")
            finally:
                controller.close()
                server.shutdown()
                server.server_close()
                thread.join(5)
