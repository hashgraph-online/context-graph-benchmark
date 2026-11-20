"""Named deterministic descriptive reductions, retaining independent clusters."""

from collections import defaultdict
import math
import random
import statistics


def interval(values):
    values = sorted(values)
    return [values[math.floor((len(values) - 1) * .025)], values[math.floor((len(values) - 1) * .975)]]


def wilson(events, trials):
    if not trials:
        return None
    z, p = 1.959963984540054, events / trials
    divisor = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / divisor
    radius = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / divisor
    return [max(0, center - radius), min(1, center + radius)]


def describe(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"count": 0, "median": None, "p95": None}
    return {"count": len(values), "median": statistics.median(values),
            "p95": values[max(0, math.ceil(.95 * len(values)) - 1)]}


def paired_recall(rows, baseline, candidate, domains, seed, draws=10000):
    repeated = defaultdict(list)
    for row in rows:
        repeated[(row["adapter"], row["task"])].append(row)
    indexed = {key: {**values[0], "recall": statistics.mean(v["recall"] for v in values) if all(v["recall"] is not None for v in values) else None} for key, values in repeated.items()}
    groups = {domain: defaultdict(list) for domain in domains}
    for row in indexed.values():
        if row["adapter"] != baseline or row["recall"] is None:
            continue
        other = indexed.get((candidate, row["task"]))
        if other is None or other["recall"] is None:
            return {"difference": None, "ci95": None, "reason": "incomplete-pairs"}
        groups[row["domain"]][row["cluster"]].append(other["recall"] - row["recall"])
    counts = {domain: len(groups[domain]) for domain in domains}
    if any(not groups[d] for d in domains):
        return {"difference": None, "ci95": None, "reason": "unavailable-domain", "clusters": counts}
    difference = statistics.mean(statistics.mean(v for cluster in groups[d].values() for v in cluster) for d in domains)
    result = {"difference": difference * 100, "ci95": None, "reason": "fewer-than-eight-clusters", "clusters": counts,
              "estimand": "equal-domain-task-mean-difference.v1", "draws": draws, "seed": seed}
    if min(counts.values()) >= 8:
        rng = random.Random(seed)
        samples = []
        for _ in range(draws):
            means = []
            for domain in domains:
                clusters = [groups[domain][key] for key in sorted(groups[domain])]
                selected = [clusters[rng.randrange(len(clusters))] for _ in clusters]
                means.append(statistics.mean(v for cluster in selected for v in cluster))
            samples.append(statistics.mean(means) * 100)
        result.update(ci95=interval(samples), reason="descriptive-power-not-qualified")
    return result


def paired_performance(rows, baseline, candidate, seed, sampling_unit, draws=10000):
    indexed = {(r["adapter"], r["task"], r.get("replicate", 0)): r for r in rows}
    result = {"sampling_unit": sampling_unit, "metrics": {}, "scope": "Fresh process pair per cell; startup and one query, no steady-state/power-loss claim."}
    for metric in ("query_ms", "startup_ms"):
        pairs = []
        incomplete = False
        for row in rows:
            if row["adapter"] != baseline:
                continue
            other = indexed.get((candidate, row["task"], row.get("replicate", 0)))
            if other is None or row["status"] != "ok" or other["status"] != "ok" or not row[metric] or other[metric] is None:
                incomplete = True
                continue
            pairs.append((row[metric], other[metric]))
        if incomplete or not pairs:
            result["metrics"][metric] = {"pairs": len(pairs), "ratio_of_medians": None, "median_paired_ratio": None, "ci95": None, "reason": "incomplete-or-failed-pair"}
            continue
        ratios = [candidate_value / baseline_value for baseline_value, candidate_value in pairs]
        summary = {"pairs": len(pairs), "ratio_of_medians": statistics.median(v[1] for v in pairs) / statistics.median(v[0] for v in pairs),
                   "median_paired_ratio": statistics.median(ratios), "ci95": None, "reason": "descriptive-without-process-cohort-inference"}
        if sampling_unit == "process-cohort" and len(pairs) >= 8:
            rng = random.Random(seed)
            summary.update(ci95=interval([statistics.median(ratios[rng.randrange(len(ratios))] for _ in ratios) for _ in range(draws)]), reason="descriptive-power-not-qualified")
        result["metrics"][metric] = summary
    return result
