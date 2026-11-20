"""Evidence-unit coverage; extra copies cannot manufacture useful facts."""

import statistics
from collections import Counter

from ..statistics import describe


def span_covered(gold, delivered):
    position = gold["start"]
    for span in sorted(delivered, key=lambda s: s["start"]):
        if span["document_id"] != gold["document_id"] or span["version"] != gold["version"]:
            continue
        if span["start"] <= position < span["end"]:
            position = span["end"]
        if position >= gold["end"]:
            return True
    return False


def covered_units(units, delivered):
    return [unit for unit in units if any(all(span_covered(span, delivered) for span in alternative) for alternative in unit["alternatives"])]


def reduce_rows(rows, suite):
    domains = {}
    for domain in suite["domains"]:
        eligible = [row for row in rows if row["domain"] == domain and row["recall"] is not None]
        domains[domain] = {"tasks": len({r.get("task", index) for index, r in enumerate(rows) if r["domain"] == domain}),
                           "evidence_tasks": len({r.get("task", index) for index, r in enumerate(eligible)}), "evidence_observations": len(eligible),
                           "recall": statistics.mean(r["recall"] for r in eligible) if eligible else None,
                           "covered_units": sum(r["covered_units"] for r in eligible),
                           "gold_units": sum(r["gold_units"] for r in eligible)}
    values = [domains[domain]["recall"] for domain in suite["domains"]]
    score = None if any(v is None for v in values) else 100 * statistics.mean(values)
    families = {}
    if suite["mode"] == "graph":
        for family in suite["families"]:
            eligible = [row for row in rows if row["family"] == family]
            families[family] = {"episodes": len(eligible), "passed": sum(r["graph_pass"] for r in eligible),
                                "success": statistics.mean(r["graph_pass"] for r in eligible) if eligible else None}
        scores = [item["success"] for item in families.values()]
        graph_score = None if not scores or any(v is None for v in scores) else 100 * statistics.mean(scores)
    else:
        graph_score = None
    failures = Counter(row["status"] for row in rows)
    violations = Counter(v for row in rows for v in row["violations"])
    eligible = [r for r in rows if r["recall"] is not None]
    contradiction_total = sum(r.get("contradiction_units", 0) for r in rows)
    return {"evidence_score": score, "evidence_metric": "evidence-unit-domain-macro.v1",
            "graph_score": graph_score, "graph_metric": "seven-family-episode-success.v1" if suite["mode"] == "graph" else None,
            "domains": domains, "families": families, "statuses": dict(sorted(failures.items())),
            "known_evidence_tasks": len(eligible), "unannotated_tasks": sum(r.get("expectation") == "unannotated" for r in rows),
            "evidence_hit_rate": sum(r["covered_units"] > 0 for r in eligible) / len(eligible) if eligible else None,
            "complete_evidence_rate": sum(r["covered_units"] == r["gold_units"] for r in eligible) / len(eligible) if eligible else None,
            "annotation_precision": statistics.mean(r["annotation_precision"] for r in eligible) if eligible and all("annotation_precision" in r for r in eligible) else None,
            "contradiction_recall": sum(r.get("covered_contradiction_units", 0) for r in rows) / contradiction_total if contradiction_total else None,
            "violations": dict(sorted(violations.items())), "context_tokens": sum(r["tokens"] for r in rows),
            "query_ms": describe([r["query_ms"] for r in rows]), "startup_ms": describe([r["startup_ms"] for r in rows]),
            "memory_bytes": None, "cost_currency": None, "compliant": not bool(violations) and all(r["status"] in ("ok", "abstained", "refused") for r in rows),
            "capability_coverage": sum(r["status"] != "unsupported" for r in rows) / len(rows) if rows else None}
