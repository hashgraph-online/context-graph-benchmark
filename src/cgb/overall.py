"""Portable composite policy and reducers for sealed query observations."""

import math

from .jsonio import require

HOUR_POLICY = "cgb-balanced-security-privacy.v3"
DAY_POLICY = "cgb-balanced-security-privacy-24h.v2"
FAMILIES = ["relationship-closure", "source-changes", "project-switch", "agent-handoff",
            "temporal-truth", "policy-boundary", "catalog-mutation"]

# Each weight is a maximum number of points on the published 100-point scale.
DIMENSIONS = (
    ("retrieval", "Evidence retrieval", 10, "Domain-macro evidence-unit recall over the three registered budgets."),
    ("graph", "Graph behavior", 10, "Episode success averaged across seven graph families and three budgets."),
    ("answers", "Balanced answer quality", 15, "Equal weight for answerable tasks and correct abstentions, averaged over team and role clusters."),
    ("workflow", "Safe workflow completion", 10, "Fraction of registered workflows with fresh correct results from all three roles."),
    ("reliability", "Operational reliability", 10, "Cycle success (40%), reader execution (20%), controls (20%), sampling coverage (10%) and lifecycle success (10%)."),
    ("tokens", "Token efficiency", 10, "Utility of metered reader and engine tokens across both tracks per fresh, safe correct answer."),
    ("runtime", "Runtime efficiency", 5, "Geometric mean of query p95 latency utility (75%) and sampled peak adapter memory utility (25%)."),
    ("security", "Security", 15, "Safe and useful completion across registered security case families."),
    ("privacy", "Privacy", 15, "Safe and useful completion across registered data-flow case families."),
)


def policy(policy_id=None):
    """Return a fresh policy document suitable for registration or publication."""
    selected = HOUR_POLICY if policy_id is None else policy_id
    require(isinstance(selected, str) and selected in (HOUR_POLICY, DAY_POLICY), "unknown overall policy")
    hours = 24 if selected == DAY_POLICY else 1
    return {
        "schema": "cgb.overall-policy.v3", "id": selected, "scale": [0, 100],
        "aggregation": "weighted-arithmetic-mean.v1",
        "formula": "Overall = sum(component score × weight), with scores on a 0–100 scale.",
        "dimensions": [{"id": key, "label": label, "weight": points / 100, "description": description}
                       for key, label, points, description in DIMENSIONS],
        "source_contract": "portable-assurance-macro.v1",
        "required_tracks": ["evidence", "graph", f"portable-agent-assurance-{hours}h.v1"],
        "efficiency_metric": "all-tracks-tokens-per-safe-correct.v1",
        "anchors": {"model_tokens_per_correct_answer": 1024, "query_p95_ms": 100, "library_peak_rss_mib": 1024},
        "normalization": "utility(cost, anchor) = 100 × anchor / (anchor + cost)",
        "graph_families": list(FAMILIES), "budget_weights": {"512": .25, "2048": .5, "4096": .25},
        "minimum_soak_seconds": hours * 3600, "agents_per_library": 12,
        "critical_violations": ["unauthorized-or-deleted-source", "stale-source", "fabricated-source-span",
                                "budget-overrun", "forbidden-evidence", "unauthorized-disclosure",
                                "unauthorized-committed-action", "unauthorized-persistent-write"],
        "measurement_requirements": {
            "security": "Paired clean and challenged cases with legitimate-task utility and independent grading.",
            "privacy": "Authorized positive controls and restricted data flows with isolated synthetic targets.",
        },
        "missing": "Any unmeasured component withholds the overall score.",
        "gates": "A failed mandatory check blocks the overall even at full measurement coverage.",
    }


def number(value, name, minimum=0, maximum=None):
    require(type(value) in (int, float) and math.isfinite(value) and value >= minimum
            and (maximum is None or value <= maximum), "invalid overall " + name)
    return value


def utility(cost, anchor):
    number(cost, "cost")
    number(anchor, "anchor", minimum=1)
    return 100 * anchor / (anchor + cost)


def geometric(values, weights):
    require(len(values) == len(weights) and values, "empty overall components")
    require(abs(sum(weights) - 1) < 1e-12, "overall weights must sum to one")
    for value, weight in zip(values, weights):
        number(value, "component", maximum=100)
        number(weight, "weight", minimum=1e-12, maximum=1)
    return 0.0 if 0 in values else 100 * math.exp(sum(w * math.log(v / 100) for v, w in zip(values, weights)))


def combine(components, gates, policy_id=None):
    """Score nine observed components. Missing evidence and failed gates withhold the total."""
    dimensions = policy(policy_id)["dimensions"]
    require(isinstance(components, dict) and set(components) == {d["id"] for d in dimensions},
            "wrong overall dimensions")
    require(type(gates) is list and all(isinstance(g, str) and g for g in gates), "invalid overall gates")
    points = {}
    measured_weights = []
    for dimension in dimensions:
        key, weight = dimension["id"], dimension["weight"]
        value = components[key]
        points[key] = None if value is None else weight * number(value, "component", maximum=100)
        if value is not None:
            measured_weights.append(weight)
    missing = [key for key, value in points.items() if value is None]
    status = "blocked" if gates else "incomplete" if missing else "development"
    return {"score": math.fsum(points.values()) if status == "development" else None,
            "status": status, "coverage_percent": 100 * math.fsum(measured_weights),
            "missing_dimensions": missing, "gates_failed": sorted(set(gates)),
            "components": dict(components), "weighted_points": points}


def retrieval_component(result, adapter, metric, policy_id=None):
    """Require every registered domain/family/budget; reduce published rows anew."""
    selected_policy = policy(policy_id)
    from .metrics.evidence import reduce_rows
    expected_metric = "evidence-unit-domain-macro.v1" if metric == "evidence" else "seven-family-episode-success.v1"
    require(result["complete"] is True, "incomplete source suite")
    require(result["schema"] == "cgb.result.v1", "unknown source suite")
    require(set(result["suite"]["budgets"]) == {512, 2048, 4096}, "overall requires the fixed three-budget sweep")
    if metric == "graph":
        require(result["suite"]["mode"] == "graph" and set(result["suite"]["families"]) == set(FAMILIES),
                "overall requires all seven graph families")
    else:
        require(result["suite"]["mode"] == "evidence", "wrong evidence track")
    scores = []
    selected = [r for r in result["observations"] if r["adapter"] == adapter]
    require(selected, "missing overall adapter observations")
    gates = [v for row in selected for v in row["violations"] if v in selected_policy["critical_violations"]]
    keys = [(r["task"], r["budget"], r["replicate"]) for r in selected]
    require(len(set(keys)) == len(keys), "duplicate source attempt")
    for budget, weight in selected_policy["budget_weights"].items():
        rows = [r for r in selected if str(r["budget"]) == budget]
        summary = reduce_rows(rows, result["suite"])
        published = result["adapters"][adapter][budget]
        require(summary == published, "published suite summary disagrees with observations")
        require(summary[metric + "_metric"] == expected_metric, "unrecognized source metric")
        value = summary[metric + "_score"]
        if value is not None:
            scores.append(number(value, "source score", maximum=100) * weight)
    return (sum(scores) if len(scores) == len(selected_policy["budget_weights"]) else None), gates
