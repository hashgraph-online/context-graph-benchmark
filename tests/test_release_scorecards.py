"""The release gate must distinguish unknown cost from a measured zero."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cgb.jsonio import Invalid, read_json, write_json
from cgb.reference import verify, verify_row


ROOT = Path(__file__).resolve().parents[1]


class ReleaseScorecards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = read_json(ROOT / "src/cgb/data/reference-study.json")
        cls.receipt = read_json(ROOT / "src/cgb/data/reference-study-provenance.json")

    def check(self, row, sample):
        verify_row(row, sample, self.result["policy"], self.receipt["method"])

    def test_release_requires_all_native_usage_and_registered_denominators(self):
        row, sample = self.result["systems"][0], self.receipt["systems"][0]
        for change in ("missing usage", "changed denominator", "changed answer count", "partial score"):
            with self.subTest(change=change):
                measured, cost = deepcopy(row), deepcopy(sample)
                if change == "missing usage":
                    cost["model_usage"]["routine"]["output_tokens"] = None
                elif change == "changed denominator":
                    cost["all_tracks_tokens_per_safe_correct_answer"]["correct_answers"] += 1
                elif change == "changed answer count":
                    next(iter(cost["answer_clusters"]["answerable"].values()))["registered"] += 1
                else:
                    measured.update(status="incomplete", score=None, missing_dimensions=["tokens"])
                    measured["components"]["tokens"] = None
                with self.assertRaises(Invalid):
                    self.check(measured, cost)

    def test_reduction_preserves_a_measured_zero(self):
        row = deepcopy(next(row for row in self.result["systems"] if row["id"] == "lean-ctx"))
        sample = next(sample for sample in self.receipt["systems"] if sample["engine"] == "lean-ctx")
        self.check(row, sample)
        self.assertEqual(row["components"]["retrieval"], 0)
        self.assertEqual(row["weighted_points"]["retrieval"], 0)
        self.assertAlmostEqual(row["score"], 80.64116056008515)
        row["score"] = 0
        with self.assertRaises(Invalid):
            self.check(row, sample)

    def test_installed_reference_verifies_offline(self):
        self.assertEqual(verify(), self.result)

    def test_scorecard_must_identify_its_measurements_and_policy_selection(self):
        with TemporaryDirectory() as directory:
            for field in ("source", "policy_selection"):
                with self.subTest(field=field):
                    result = deepcopy(self.result)
                    if field == "source":
                        result["source"]["manifest_sha256"] = "0" * 64
                    else:
                        result["policy_selection"] = "before-collection"
                    path = Path(directory) / (field + ".json")
                    write_json(path, result)
                    with self.assertRaises(Invalid):
                        verify(result_path=path)

    def test_parameter_extract_is_bound_to_the_measurement_receipt(self):
        with TemporaryDirectory() as directory:
            design = read_json(ROOT / "src/cgb/data/reference-study-design.json")
            design["method"]["seed"] += 1
            path = Path(directory) / "design.json"
            write_json(path, design)
            with self.assertRaisesRegex(Invalid, "study design differs"):
                verify(design_path=path)
