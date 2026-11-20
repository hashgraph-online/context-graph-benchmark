"""Dashboard availability and ordering contracts; no engine or model results are synthesized."""

from pathlib import Path
import tempfile
import unittest

from cgb import __version__
from cgb.evidence import seal
from cgb.jsonio import write_json
from cgb.platform import answer_metric_summary, collect


class PlatformReadiness(unittest.TestCase):
    def test_completed_reader_runs_with_unknown_usage_do_not_claim_measured_cost(self):
        runs = [{"status": "completed", "reader": {"model": "test"}, "rows": [
            {"model_tokens_per_correct_answer": {"value": None}}, {}]}]
        result = answer_metric_summary(runs, [])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["measured_results"], 0)
        self.assertEqual(result["missing_results"], 2)

    def test_partial_cost_keeps_zero_and_unknown_distinct_and_ignores_unfinished_runs(self):
        runs = [{"status": "completed", "reader": {"model": "test"}, "rows": [
            {"model_tokens_per_correct_answer": {"value": 0}},
            {"model_tokens_per_correct_answer": {"value": None}},
            {"model_tokens_per_correct_answer": {"value": False}},
            {"model_tokens_per_correct_answer": {"value": -1}}]},
            {"status": "failed", "reader": {"model": "test"}, "rows": [
                {"model_tokens_per_correct_answer": {"value": 10}}]}]
        pairs = [{"status": "completed", "reader": {"model": "test"},
                  "rows": [{"tokens_per_safe_correct_answer": 120}]}]
        result = answer_metric_summary(runs, pairs)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["completed_runs"], 2)
        self.assertEqual(result["system_results"], 5)
        self.assertEqual(result["measured_results"], 2)
        self.assertEqual(result["missing_results"], 3)

    def test_empty_or_context_only_runs_do_not_supply_model_cost(self):
        self.assertEqual(answer_metric_summary([], [])["status"], "unavailable")
        run = {"status": "completed", "reader": None,
               "rows": [{"model_tokens_per_correct_answer": {"value": 0}}]}
        self.assertEqual(answer_metric_summary([run], [])["system_results"], 0)
        pair = {"status": "completed", "reader": {"model": "test"},
                "rows": [{"tokens_per_safe_correct_answer": 120}]}
        self.assertEqual(answer_metric_summary([], [pair])["status"], "available")

    def test_partial_state_and_large_json_numbers_do_not_break_the_collector(self):
        runs = [{"status": "completed", "reader": {"model": "test"}, "rows": None},
                {"status": "completed", "reader": {"model": "test"}, "rows": [
                    None, {"model_tokens_per_correct_answer": {"value": 10**1000}},
                    {"model_tokens_per_correct_answer": {"value": float("inf")}}]}]
        result = answer_metric_summary(runs, [])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["measured_results"], 1)
        self.assertEqual(result["missing_results"], 2)

    def test_nested_studies_and_standalone_runs_are_ordered_by_recorded_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for identity, started in (("older", "2026-01-01T00:00:00Z"),
                                      ("newer", "2026-01-03T00:00:00Z")):
                write_json(root / "lab/studies" / identity / "state.json", {
                    "schema": "cgb.portable-macro-state.v1", "id": identity,
                    "started_at": started, "status": "failed"})
                for leaf, schema in (("comparison", "cgb.platform-comparison.v1"),
                                     ("soak", "cgb.portable-soak-state.v1")):
                    write_json(root / "lab/studies" / identity / leaf / "state.json", {
                        "schema": schema, "id": identity, "started_at": started,
                        "status": "failed"})
                # These empty UI metadata fixtures contain no engine observations or score.
                write_json(root / "lab/studies" / identity / "result.json", {
                    "schema": "cgb.overall-result.v4", "id": identity})
                seal(root / "lab/studies" / identity, "report")
            write_json(root / "lab/comparisons/middle/state.json", {
                "schema": "cgb.platform-comparison.v1", "id": "middle",
                "started_at": "2026-01-02T00:00:00Z", "status": "failed"})
            result = collect(root)
            self.assertEqual([r["id"] for r in result["comparisons"]], ["newer", "middle", "older"])
            self.assertEqual([r["id"] for r in result["soaks"]], ["newer", "older"])
            self.assertEqual([r["id"] for r in result["overall_studies"]], ["newer", "older"])
            self.assertEqual(result["version"], __version__)
            self.assertEqual(result["answer_metrics"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
