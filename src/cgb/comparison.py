"""Portable one-shot comparison orchestrator; same frozen scorer for every adapter."""

from datetime import datetime, timezone
from pathlib import Path
import secrets
import statistics

from .workspace import atomic_json, inside
from .evidence import seal
from .jsonio import digest, encoded, read_json, require, write_json
from .platform import PROFILES, descriptors
from .runner import make_plan, run
from .scoring import score, replay
from .tokenization import Counter


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def settings(root, selected, profiles, budgets, replicates):
    require(type(profiles) is list and profiles and len(profiles) == len(set(profiles)) and set(profiles) <= set(PROFILES), "select evidence and/or graph profile")
    require(type(budgets) is list and budgets and len(budgets) <= 3 and len(set(budgets)) == len(budgets)
            and all(type(n) is int and 1 <= n <= 16384 for n in budgets), "invalid context budgets")
    require(type(replicates) is int and 1 <= replicates <= 20, "replicates must be 1–20")
    counter = Counter(inside(root, ".cgb/tokenizer"))
    return descriptors(root, selected), counter


def summarize(results, policy_id=None):
    from .statistics import describe
    from .overall import combine, policy, retrieval_component
    selected_policy = policy(policy_id)
    values = {}
    for result in results:
        for treatment in result["treatments"]:
            key = treatment["id"]
            current = values.setdefault(key, {"id": key, "engine": treatment["engine"], "version": treatment["version"],
                                             "configuration": treatment["configuration"], "profiles": {}, "observations": []})
            metric = "graph_score" if result["suite"]["mode"] == "graph" else "evidence_score"
            scores = [v[metric] for v in result["adapters"][key].values()]
            current["profiles"]["graph" if metric == "graph_score" else "evidence"] = {
                "score": statistics.mean(scores) if scores and all(s is not None for s in scores) else None,
                "budgets": result["adapters"][key], "metric": metric, "evidence_class": result["evidence_class"],
                "suite_id": result["suite"]["id"],
                "task_count": len({r["task"] for r in result["observations"] if r["adapter"] == key})}
            current["observations"].extend(r for r in result["observations"] if r["adapter"] == key)
    for current in values.values():
        rows = current.pop("observations")
        components = {d["id"]: None for d in selected_policy["dimensions"]}
        gates = [v for row in rows for v in row["violations"] if v in selected_policy["critical_violations"]]
        for source in results:
            if current["id"] not in source["adapters"] or set(source["suite"]["budgets"]) != {512, 2048, 4096}:
                continue
            metric = "graph" if source["suite"]["mode"] == "graph" else "evidence"
            if metric == "graph" and set(source["suite"].get("families", [])) != set(selected_policy["graph_families"]):
                continue
            component, failed = retrieval_component(source, current["id"], metric, selected_policy["id"])
            components["graph" if metric == "graph" else "retrieval"] = component
            gates.extend(failed)
        assessment = combine(components, gates, selected_policy["id"])
        current.update(query_ms=describe([r["query_ms"] for r in rows]), startup_ms=describe([r["startup_ms"] for r in rows]),
                       attempts=len(rows), failures=sum(r["status"] not in ("ok", "refused", "abstained") for r in rows),
                       violations=sum(len(r["violations"]) for r in rows), context_tokens=sum(r["tokens"] for r in rows),
                       overall_score=assessment["score"], overall_status=assessment["status"],
                       overall_policy=selected_policy["id"], overall_components=components,
                       overall_missing_dimensions=assessment["missing_dimensions"],
                       overall_coverage_percent=assessment["coverage_percent"], overall_gates_failed=assessment["gates_failed"],
                       tokens_per_correct_answer=None,
                       tokens_saved_per_correct_answer=None)
    return list(values.values())


def compare(root, selected, name="Context graph comparison", profiles=None, budgets=None, replicates=1, output=None, *, policy_id=None):
    from .overall import policy
    selected_policy = policy(policy_id)
    root = Path(root).absolute()
    profiles, budgets = profiles or ["evidence", "graph"], budgets or [512, 2048, 4096]
    adapter_paths, counter = settings(root, selected, profiles, budgets, replicates)
    require(isinstance(name, str) and 0 < len(name.strip()) <= 120, "invalid comparison name")
    identity = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    destination = Path(output).absolute() if output else inside(root, "lab/comparisons/" + identity)
    destination.mkdir(parents=True, exist_ok=False)
    plans = {p: make_plan(root / PROFILES[p]["suite"], adapter_paths, root / ".cgb/tokenizer",
                          seed=41, budgets=budgets, replicates=replicates, timeout_ms=30000) for p in profiles}
    state = {"schema": "cgb.platform-comparison.v1", "id": destination.name, "name": name.strip(), "status": "running",
             "started_at": stamp(), "updated_at": stamp(), "engines": selected, "profiles": profiles,
             "budgets": budgets, "replicates": replicates, "completed": 0, "total": sum(len(p["cells"]) for p in plans.values()),
             "reports": [], "rows": [], "replay": "pending", "comparative_qualified": False,
             "overall_policy": selected_policy["id"], "overall_policy_sha256": digest(encoded(selected_policy)),
             "summary": "Public development comparison. No complete overall score from retrieval tests alone."}
    config = {"schema": "cgb.platform-comparison-plan.v1", "plans": plans, "tokenizer": counter.identity,
              "overall_policy": selected_policy, "overall_policy_sha256": digest(encoded(selected_policy)),
              "cohort": {"engines": selected, "profiles": profiles, "budgets": budgets, "seed": 41, "replicates": replicates}}
    write_json(destination / "registration.json", config)
    results = []
    def save():
        state["updated_at"] = stamp()
        atomic_json(destination / "state.json", state)
    save()
    try:
        for profile, plan in plans.items():
            offset = state["completed"]
            def progress(value):
                state["completed"] = offset + value["completed"]
                save()
            run_path = destination / profile / "run"
            report_path = destination / profile / "report"
            run(plan, run_path, root / ".cgb/tokenizer", progress=progress)
            score(run_path, root / PROFILES[profile]["labels"], report_path)
            replay(run_path, report_path)
            result = read_json(report_path / "result.json")
            results.append(result)
            state["reports"].append({"profile": profile, "run": str(run_path.relative_to(destination)),
                                     "report": str(report_path.relative_to(destination)), "plan_sha256": result["plan_sha256"],
                                     "evaluator_sha256": result["evaluator_sha256"]})
            state["rows"] = summarize(results, selected_policy["id"])
            state["completed"] = offset + len(plan["cells"])
            save()
        state.update(status="completed", replay="passed", finished_at=stamp())
        write_json(destination / "result.json", {k: v for k, v in state.items() if k != "updated_at"})
        save()
        seal(destination, "platform-comparison")
    except BaseException as exc:
        state.update(status="stopped" if isinstance(exc, KeyboardInterrupt) else "failed", summary=type(exc).__name__ + ": " + str(exc), finished_at=stamp())
        save()
        raise
    return state


def replay_comparison(path):
    from .evidence import verify
    from .overall import policy
    path = Path(path).absolute()
    original = verify(path)
    require(original["kind"] == "platform-comparison", "wrong comparison bundle kind")
    state = read_json(path / "result.json")
    require(state["status"] == "completed" and state["schema"] == "cgb.platform-comparison.v1", "incomplete comparison")
    registration = read_json(path / "registration.json")
    require("overall_policy" in registration, "comparison registration is missing its scoring policy")
    selected_policy = policy(registration["overall_policy"]["id"])
    require(registration["overall_policy"] == selected_policy
            and registration["overall_policy_sha256"] == digest(encoded(selected_policy))
            and state.get("overall_policy") == selected_policy["id"]
            and state.get("overall_policy_sha256") == registration["overall_policy_sha256"], "comparison policy binding differs")
    results = []
    for entry in state["reports"]:
        run_path, report_path = inside(path, entry["run"]), inside(path, entry["report"])
        replay(run_path, report_path)
        results.append(read_json(report_path / "result.json"))
    require(summarize(results, selected_policy["id"]) == state["rows"], "comparison reduction differs")
    require(verify(path) == original, "comparison changed during replay")
    return {"replay": "passed", "run": str(path), "profiles": len(results)}
