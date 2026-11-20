"""Portable engine neutrality, real process/HTTP contracts and independent reductions.

The local HTTP service below is explicitly a deterministic test double, not an
AI model. Its outputs must never be published as model benchmark measurements.
"""

import copy
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest

from cgb.comparison import compare, replay_comparison
from cgb.contracts import validate, workload, labels
from cgb.jsonio import Invalid, decode, digest, encoded, read_json, write_json
from cgb.leanctx import ranked_ids, environment
from cgb.platform import collect, engines, register
from cgb.platform_dashboard import export
from cgb.portable_soak import benchmark, phase, validate_settings
from cgb.portable_soak_scoring import evaluate
from cgb.tokenization import Counter

ROOT = Path(__file__).resolve().parents[1]


def workspace(directory):
    root = Path(directory)
    for part in ("suites", "datasets/synthetic", "adapters", "profiles"):
        shutil.copytree(ROOT / part, root / part)
    (root / ".cgb/tokenizer").mkdir(parents=True)
    shutil.copyfile(ROOT / ".cgb/tokenizer/o200k_base.tiktoken", root / ".cgb/tokenizer/o200k_base.tiktoken")
    register(root, "test-reference", root / "adapters/bm25.json")
    return root


class PortableContracts(unittest.TestCase):
    def test_reference_scorecard_binds_the_measurements_and_leaves_local_runs_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            state = collect(Path(directory))
        result = state["reference_study"]
        receipt = read_json(ROOT / "src/cgb/data/reference-study-provenance.json")
        self.assertEqual(result["source"]["manifest_sha256"], receipt["sealed_files_sha256"]["manifest.json"])
        self.assertEqual(result["evidence_class"], "public-development")
        self.assertEqual(result["policy_selection"], "post-collection")
        self.assertEqual(result["id"], receipt["study"])
        self.assertFalse(result["comparative_qualified"])
        self.assertEqual(state["studies"], [])
        self.assertEqual(state["overall_studies"], [])
        self.assertEqual(state["answer_metrics"]["system_results"], 0)
        self.assertEqual([row["id"] for row in result["systems"]], ["cigar", "networkx", "lean-ctx"])
        for row in result["systems"]:
            self.assertEqual(row["coverage_percent"], 100)
            self.assertEqual(len(row["components"]), 9)
            self.assertTrue(all(isinstance(v, (int, float)) for v in [row["score"], *row["components"].values()]))
            self.assertEqual(row["missing_dimensions"], [])

    def test_four_domain_fixtures_and_day_profile_are_explicit(self):
        public = workload(read_json(ROOT / "datasets/synthetic/portable-evidence-workload.json"))
        oracle = labels(read_json(ROOT / "datasets/synthetic/portable-evidence-labels.json"), public)
        validate("suite", read_json(ROOT / "suites/portable-evidence-v1.json"))
        self.assertEqual(len(public["tasks"]), 32)
        self.assertEqual(len({t["domain"] for t in public["tasks"]}), 4)
        self.assertEqual(oracle["evidence_class"], "public-development")
        for hours in (1, 24):
            profile = read_json(ROOT / ("profiles/portable-agent-" + str(hours) + "h.v1.json"))
            self.assertEqual(profile["seconds_per_engine"], hours * 3600)
            self.assertEqual(profile["agents"], 12)
        source, _, targets = phase(1439)
        self.assertEqual(source["phase"], 1439)
        self.assertEqual(len(source["tasks"]), 12)
        self.assertEqual(targets["tasks"]["p1439/agent-2"]["state"]["policy_revision"], 1439)

    def test_leanctx_maps_only_native_known_result_headers_and_never_falls_back(self):
        name = "doc-" + "a" * 64 + ".md"
        text = 'Semantic search (BM25): "query" (1 results from 2 indexed chunks in /fixture)\n\n--- Result 1 (score: 1.42) ---\n' + name + ' :: ' + name + '#chunk-0 [Module] (L1-1)\nsource text\n'
        self.assertEqual(ranked_ids(text, {name: "source-a"}), ["source-a"])
        self.assertEqual(ranked_ids('Semantic search (BM25): "q" (0 results from 2 indexed chunks in /fixture)\n', {}), [])
        for bad, mapping in [(text, {}), ("BACKEND_REQUIRED: missing", {}), (text.replace(name, "other.md"), {name: "source-a"})]:
            with self.subTest(bad=bad[:50]), self.assertRaises(Invalid):
                ranked_ids(bad, mapping)
        env = environment("/fixture/owned")
        self.assertEqual(env["LEAN_CTX_DATA_DIR"], "/fixture/owned/data")
        self.assertNotIn("HOME", env)
        self.assertNotIn("CODEX_HOME", env)
        self.assertEqual(env["DO_NOT_TRACK"], "1")

    def test_any_registered_engine_can_run_and_replay_without_cigar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = workspace(directory)
            result = compare(root, ["test-reference"], profiles=["evidence"], budgets=[512])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["completed"], 32)
            self.assertIsNone(result["rows"][0]["overall_score"])
            self.assertEqual(result["overall_policy"], "cgb-balanced-security-privacy.v3")
            self.assertIsNone(result["rows"][0]["overall_components"]["privacy"])
            self.assertIn("security", result["rows"][0]["overall_missing_dimensions"])
            self.assertEqual(result["rows"][0]["overall_coverage_percent"], 0)
            registration = read_json(root / "lab/comparisons" / result["id"] / "registration.json")
            self.assertEqual({d["id"]: d["weight"] for d in registration["overall_policy"]["dimensions"]}["reliability"], .10)
            self.assertEqual(replay_comparison(root / "lab/comparisons" / result["id"])["replay"], "passed")
            self.assertFalse(next(e for e in engines(root) if e["id"] == "cigar")["available"])
            with self.assertRaises(Invalid):
                register(root, "../escape", root / "adapters/bm25.json")

    def test_complete_query_budget_sweep_is_partial_overall_without_assurance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = workspace(directory)
            result = compare(root, ["test-reference"], profiles=["evidence"], budgets=[512, 2048, 4096])
            row = result["rows"][0]
            self.assertIsInstance(row["overall_components"]["retrieval"], (int, float))
            self.assertAlmostEqual(row["overall_coverage_percent"], 10)
            self.assertEqual(row["overall_status"], "incomplete")
            self.assertIsNone(row["overall_score"])
            self.assertIsNone(row["overall_components"]["security"])
            self.assertIsNone(row["overall_components"]["privacy"])
            self.assertEqual(replay_comparison(root / "lab/comparisons" / result["id"])["replay"], "passed")

    def test_offline_platform_snapshot_is_data_only_and_escapes_hostile_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "lab/comparisons/hostile/state.json", {"schema": "cgb.platform-comparison.v1", "id": "hostile",
                       "name": '</script><img src=x onerror="alert(1)">', "engines": [], "status": "failed", "rows": [], "started_at": "2026-01-01T00:00:00Z"})
            export(root, root / "snapshot.html")
            text = (root / "snapshot.html").read_text()
            self.assertNotIn('</script><img', text)
            self.assertIn("connect-src 'none'", text)
            self.assertIn("script-src 'sha256-", text)
            self.assertIn('window.CGB_PLATFORM_SNAPSHOT=', text)
            self.assertNotIn('<meta name="cgb-control-token"', text)


class PortableSoak(unittest.TestCase):
    def test_actual_twelve_lanes_fixed_http_reader_failure_cost_and_replay(self):
        class Reader(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                payload, _ = json.JSONDecoder().raw_decode(request["messages"][-1]["content"])
                fields = payload["fields"]
                answer = ({"owner": "Mira", "deploy_units": 20, "dependency_ready": False} if "owner" in fields else
                          {"region": "west", "artifact": "package-0-r0", "rollback": "stable-0-r0", "deploy_units": 20} if "region" in fields else
                          {"can_release": False, "decision": "hold", "policy_revision": 0})
                body = json.dumps({"model": "explicit-test-double", "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Reader)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = workspace(directory)
                reader = {"schema": "cgb.local-reader.v1", "id": "explicit-test-double", "model": "explicit-test-double",
                          "revision": "test-fixture-only", "backend": "openai-compatible", "base_url": "http://127.0.0.1:" + str(server.server_address[1]) + "/v1",
                          "context_window": 32768, "settings": {"temperature": 0, "top_p": 1, "max_tokens": 128, "enable_thinking": False}, "artifacts": []}
                write_json(root / ".cgb/readers/test.json", reader)
                for hours in (1, 24):
                    value = validate_settings(root, ["test-reference"], hours)
                    self.assertEqual(value["seconds"], hours * 3600)
                state = benchmark(root, ["test-reference"], pilot_seconds=5, reader_path=".cgb/readers/test.json")
                path = root / "lab/portable-soaks" / state["id"]
                result = read_json(path / "result.json")
                self.assertEqual(state["status"], "completed")
                self.assertFalse(result["duration_qualified"])
                row = result["engines"][0]
                self.assertEqual(row["correct_cycles"], 12)
                self.assertTrue(row["cleanup_clean"])
                self.assertEqual(row["correct_answers"], 3)
                self.assertEqual(row["model_tokens_per_correct_answer"]["value"], 120)
                self.assertEqual(row["tokens_saved_per_correct_answer"], 0)
                plan = read_json(path / "plan.json")
                changed_plan = copy.deepcopy(plan)
                changed_plan["overall_policy"]["dimensions"][0]["weight"] = .11
                with self.assertRaisesRegex(Invalid, "policy binding"):
                    evaluate(changed_plan, path, Counter(path / "tokenizer"))
                with gzip.open(path / "observations.jsonl.gz", "rb") as stream:
                    original = [decode(line) for line in stream]
                def reduce(changed):
                    with gzip.open(path / "observations.jsonl.gz", "wb") as stream:
                        for event in changed:
                            stream.write(encoded(event))
                    return evaluate(plan, path, Counter(path / "tokenizer"))
                changed = copy.deepcopy(original)
                last = [r for r in changed if r["kind"] == "model"][-1]
                last["response"]["text"] = '{}'
                cost = reduce(changed)["engines"][0]
                self.assertEqual(cost["correct_answers"], 2)
                self.assertEqual(cost["model_tokens_per_correct_answer"]["value"], 180)
                last["response"]["input_tokens"] = None
                self.assertIsNone(reduce(changed)["engines"][0]["model_tokens_per_correct_answer"]["value"])
                changed = copy.deepcopy(original)
                next(r for r in changed if r["kind"] == "end")["elapsed"] = 86400
                with self.assertRaises(Invalid):
                    reduce(changed)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
