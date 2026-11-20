"""Numerical contract for the portable composite."""

import math
import unittest

from cgb.jsonio import Invalid
from cgb.overall import DAY_POLICY, HOUR_POLICY, combine, policy


class Composite(unittest.TestCase):
    def setUp(self):
        self.dimensions = policy()["dimensions"]
        self.full = {d["id"]: 100 for d in self.dimensions}

    def test_every_dimension_has_exactly_its_allocated_influence(self):
        for dimension in self.dimensions:
            key = dimension["id"]
            for component in (0, 0.001, 1, 50, 99.9, 100):
                with self.subTest(key=key, component=component):
                    values = {**self.full, key: component}
                    row = combine(values, [])
                    self.assertAlmostEqual(row["score"], 100 - (100 - component) * dimension["weight"])
                    self.assertAlmostEqual(row["score"], math.fsum(row["weighted_points"].values()))
                    self.assertEqual(row["coverage_percent"], 100)

    def test_equal_values_and_multiple_zeros(self):
        for level in (0, 17.5, 100):
            values = dict.fromkeys(self.full, level)
            self.assertEqual(combine(values, [])["score"], level)
        self.assertEqual(combine({**self.full, "retrieval": 0, "graph": 0}, [])["score"], 80)

    def test_missing_and_failed_gates(self):
        for gates, expected in (([], "incomplete"), (["unauthorized-disclosure"], "blocked")):
            row = combine({**self.full, "tokens": None}, gates)
            self.assertIsNone(row["score"])
            self.assertIsNone(row["weighted_points"]["tokens"])
            self.assertEqual(row["missing_dimensions"], ["tokens"])
            self.assertEqual(row["coverage_percent"], 90)
            self.assertEqual(row["status"], expected)
        self.assertIsNone(combine(self.full, ["process-cleanup-failed"])["score"])

    def test_invalid_inputs_and_separate_day_identity(self):
        for value in (-1, 101, float("nan"), float("inf"), True, "0"):
            with self.subTest(value=value), self.assertRaises(Invalid):
                combine({**self.full, "runtime": value}, [])
        with self.assertRaises(Invalid):
            combine({"retrieval": 100}, [])
        for identity, hours in ((HOUR_POLICY, 1), (DAY_POLICY, 24)):
            self.assertEqual(policy(identity)["minimum_soak_seconds"], hours * 3600)
            self.assertEqual(policy(identity)["aggregation"], "weighted-arithmetic-mean.v1")
            self.assertEqual(combine(self.full, [], identity)["score"], 100)
