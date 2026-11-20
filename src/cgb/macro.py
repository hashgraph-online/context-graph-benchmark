"""Prospective portable macro studies with exact source bindings and offline replay."""

from datetime import datetime, timezone
from pathlib import Path
import secrets

from .workspace import atomic_json, inside
from .comparison import compare, replay_comparison, stamp
from .evidence import implementation_identity, seal, verify
from .jsonio import digest, encoded, read_json, require, write_json
from .overall import DAY_POLICY, HOUR_POLICY, combine, geometric, policy, retrieval_component, utility
from .portable_soak import benchmark as soak, replay as replay_soak, validate_settings


def settings(root, selected, reader, hours=1, pilot_seconds=None, baseline=None, isolation="auto"):
    from .comparison import settings as query_settings
    query_settings(root, selected, ["evidence", "graph"], [512, 2048, 4096], 1)
    return validate_settings(root, selected, hours, pilot_seconds, reader, baseline, True, isolation)


def components(row, result, plan, selected_policy):
    assurance = row["assurance"]
    expected_restarts = len(range(900, plan["config"]["seconds"], 900))
    models = row["registered_model_attempts"]
    require(0 <= row["controls_passed"] <= row["controls_expected"]
        and 0 <= row["restarts_clean"] <= row["restarts_observed"] <= expected_restarts
        and 0 <= row["model_executions_ok"] <= row["model_attempts"] <= models,
        "extra or inconsistent operational observations")
    values = {"answers": row["balanced_answer_quality"], "workflow": row["workflow_completion_percent"],
        "reliability": 100 * (.4*row["correct_cycles"]/row["registered_cycles"]
            + .2*row["model_executions_ok"]/models if models else 0),
        "tokens": None, "runtime": None, **assurance["components"]}
    # Fixed denominators retain missed slots. Resource coverage and lifecycle
    # weights are explicit; a pilot with no scheduled controls gets no gate pass.
    controls = row["controls_passed"]/row["controls_expected"] if row["controls_expected"] else 0
    coverage = min(1, row["resource_samples"]/(row["registered_cycles"]/12))
    lifecycle = (row["restarts_clean"]+int(row["cleanup_clean"]))/(expected_restarts+1)
    values["reliability"] += 100*(.2*controls + .1*coverage + .1*lifecycle)
    token_cost = row["all_tracks_tokens_per_safe_correct_answer"]["value"]
    if token_cost is not None:
        values["tokens"] = utility(token_cost, selected_policy["anchors"]["model_tokens_per_correct_answer"])
    p95, rss = row["query_ms"]["p95"], row["sampled_adapter_peak_rss_bytes"]
    if p95 is not None and rss is not None:
        anchors = selected_policy["anchors"]
        values["runtime"] = geometric([utility(p95, anchors["query_p95_ms"]),
                                       utility(rss / 1024**2, anchors["library_peak_rss_mib"])], [.75, .25])
    gates = list(assurance["critical_violations"])
    if row["violations"]:
        gates.append("portable-source-or-query-violation")
    if not row["cleanup_clean"]:
        gates.append("process-cleanup-failed")
    if not result["duration_qualified"]:
        gates.append("soak-duration-not-qualified")
    if not assurance["complete"]:
        gates.append("assurance-observation-incomplete")
    if not result["assurance"]["isolation"]["enforced"]:
        gates.append("label-isolation-not-enforced")
    if plan["config"]["hours"]*3600 != selected_policy["minimum_soak_seconds"]:
        gates.append("wrong-duration-profile")
    if row["controls_passed"] != row["controls_expected"]:
        gates.append("portable-control-failed")
    return values, gates


def evaluate(root):
    root = Path(root)
    registration = read_json(root / "registration.json")
    selected_policy = policy(registration["policy"]["id"])
    require(registration["schema"] == "cgb.portable-macro-plan.v1" and registration["policy"] == selected_policy,
            "macro policy binding differs")
    require(registration["evaluator_sha256"] == implementation_identity(), "matching frozen macro evaluator required")
    replay_comparison(root / "comparison")
    replay_soak(root / "soak")
    return _reduce(root, registration)


def _reduce(root, registration):
    """Provisional composition; public acceptance always replays raw sources."""
    selected_policy = policy(registration["policy"]["id"])
    soak_result, soak_plan = read_json(root / "soak/result.json"), read_json(root / "soak/plan.json")
    query_registration = read_json(root / "comparison/registration.json")
    require(query_registration["overall_policy"] == soak_plan["overall_policy"] == selected_policy,
            "query, endurance and study scoring policies differ")
    require(soak_plan["config"] == registration["config"], "macro and endurance execution settings differ")
    require(soak_plan["config"].get("assurance") and soak_plan["config"]["engines"] == registration["engines"], "macro endurance source binding differs")
    require(soak_plan["config"]["reader"] == registration["reader"] and soak_plan["evaluator_sha256"] == registration["evaluator_sha256"], "macro reader/evaluator differs")
    sources = {track: (read_json(root / "comparison" / track / "report/result.json"), read_json(root / "comparison" / track / "run/plan.json")) for track in ("evidence", "graph")}
    systems = []
    for row in soak_result["engines"]:
        engine = row["engine"]
        descriptor = soak_plan["adapters"][engine]
        values, gates = components(row, soak_result, soak_plan, selected_policy)
        for track, (result, plan) in sources.items():
            require(plan["harness_sha256"] == registration["evaluator_sha256"] and plan["tokenizer"] == soak_plan["tokenizer"], "macro source evaluator/tokenizer differs")
            matches = [d for d in plan["adapters"] if d == descriptor]
            require(len(matches) == 1, "query and soak engine/adapter/config/artifact bindings differ")
            value, failed = retrieval_component(result, descriptor["id"], track, selected_policy["id"])
            values["retrieval" if track == "evidence" else "graph"] = value
            gates.extend(failed)
        assessment = combine(values, gates, selected_policy["id"])
        systems.append({"id": engine, "engine": descriptor["engine"], "version": descriptor["version"], **assessment,
            "tokens_per_safe_correct_answer": row["all_tracks_tokens_per_safe_correct_answer"]["value"],
            "capability_boundary": "reference adapter + mock gateway; native engine authorization is not inferred"})
    return {"schema": "cgb.overall-result.v4", "id": registration["id"], "metric": selected_policy["id"], "policy": selected_policy,
        "evaluator_sha256": registration["evaluator_sha256"], "registration_sha256": digest(encoded(registration)),
        "evidence_class": "public-development", "systems": systems, "comparative_qualified": False,
        "disclosure": "One prospective local cohort, public development data, four teams and eight adversarial case templates. Repetitions are correlated; no industry ranking or certification is claimed.",
        "source_profiles": [sources[t][0]["suite"]["id"] for t in ("evidence", "graph")] + [soak_result["profile"]],
        "reader": soak_result["reader"], "resource_scope": soak_plan["resource_scope"],
        "limits": soak_result["limits"] + ["The policy is a reviewable proposal; fixed weights and anchors are not empirically validated industry consensus."]}


def benchmark(root, selected, reader, hours=1, pilot_seconds=None, baseline=None, isolation="auto", output=None):
    root = Path(root).absolute()
    config = settings(root, selected, reader, hours, pilot_seconds, baseline, isolation)
    identity = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    destination = Path(output).absolute() if output else inside(root, "lab/studies/" + identity)
    destination.mkdir(parents=True, exist_ok=False)
    registration = {"schema": "cgb.portable-macro-plan.v1", "id": destination.name, "engines": selected,
        "reader": config["reader"], "config": config, "evaluator_sha256": implementation_identity(),
        "policy": policy(DAY_POLICY if hours == 24 else HOUR_POLICY)}
    write_json(destination / "registration.json", registration)
    state = {"schema": "cgb.portable-macro-state.v1", "id": destination.name, "name": "HOL complete benchmark study",
        "status": "running", "phase": "comparison", "started_at": stamp(), "updated_at": stamp(), "engines": selected,
        "hours": hours, "pilot": pilot_seconds is not None, "reader": {k: config["reader"][k] for k in ("id", "model", "revision", "backend")},
        "rows": [], "replay": "pending", "comparative_qualified": False, "summary": "Evidence + graph + assurance endurance; fixed prospective cohort."}
    def save():
        state["updated_at"] = stamp()
        atomic_json(destination / "state.json", state)
    save()
    try:
        compare(root, selected, profiles=["evidence", "graph"], budgets=[512, 2048, 4096],
                output=destination / "comparison", policy_id=registration["policy"]["id"])
        state["phase"] = "assurance-soak"
        save()
        require(not (destination / "STOP").exists(), "study stopped before soak")
        soak(root, selected, hours, pilot_seconds, reader, config["baseline"], destination / "soak", True, config["isolation"])
        # Both child builders completed their own full independent replay.
        # Compose provisionally, then seal and replay the complete macro below.
        # Never use this helper as a substitute for public offline verification.
        result = _reduce(destination, registration)
        write_json(destination / "result.json", result)
        state.update(status="completed", phase="sealed", rows=result["systems"], finished_at=stamp(), replay="reduction-checked")
        save()
        seal(destination, "report")
        replay(destination)
    except BaseException as error:
        # A sealed record must never be rewritten after a replay error.
        if not (destination / "manifest.json").exists():
            state.update(status="failed", summary=type(error).__name__ + ": " + str(error), finished_at=stamp())
            save()
        raise
    return {**state, "path": str(destination)}


def replay(path):
    root = Path(path).absolute()
    original = verify(root)
    result = evaluate(root)
    require(encoded(result) == (root / "result.json").read_bytes(), "portable macro replay differs")
    require(verify(root) == original, "macro evidence changed during replay")
    return {"replay": "passed", "run": str(root), "comparative_qualified": False}
