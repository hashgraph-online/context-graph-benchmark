"""Streaming, independent data-only reduction of portable agent-soak evidence."""

from collections import defaultdict
import gzip
import math
from pathlib import Path

from .jsonio import MAX_LINE, decode, digest, encoded, require
from .local_models import validate_reader
from .reader_protocol import messages, handoff
from .reader_protocol import grade_target, quantiles


def rows(path, partial=False):
    total = 0
    try:
        with gzip.open(path, "rb") as stream:
            while line := stream.readline(MAX_LINE + 1):
                total += len(line)
                require(total <= 4 * 1024**3 and line.endswith(b"\n") and len(line) <= MAX_LINE, "invalid or excessive compressed journal")
                yield decode(line, MAX_LINE)
    except EOFError:
        require(partial, "truncated compressed journal")


def metric(tokens, correct, known):
    return {"value": tokens / correct if known and correct else None, "tokens": tokens if known else None,
            "correct_answers": correct, "status": "unavailable_usage" if not known else "undefined_zero_correct" if not correct else "measured"}


def query_records(root, engine):
    """Bind query observations to the original adapter protocol bytes."""
    result = {}
    for path in sorted((Path(root) / "engines" / engine).glob("generation-*/agent-*/journal.gz")):
        generation, actor = int(path.parent.parent.name.removeprefix("generation-")), int(path.parent.name.removeprefix("agent-"))
        pending = None
        for row in rows(path):
            if row["direction"] == "request":
                pending = row["message"]
            elif row["direction"] == "response-bytes":
                response = decode(bytes.fromhex(row["hex"]), MAX_LINE)
                require(pending is not None and pending["id"] == response["id"], "protocol journal request/response mismatch")
                if pending["operation"] == "query":
                    result[generation, actor, pending["id"]] = (pending["payload"], digest(encoded(response)))
                pending = None
    return result


def evaluate(plan, root, counter, partial=False):
    from .portable_soak import phase, task_payload, checked_context, SCHEDULE
    selected_policy = None
    if "overall_policy" in plan:
        from .overall import policy
        selected_policy = policy(plan["overall_policy"]["id"])
        require(plan["overall_policy"] == selected_policy
                and plan["overall_policy_sha256"] == digest(encoded(selected_policy)), "soak policy binding differs")
    config = plan["config"]
    if config.get("reader"):
        from .portable_soak import READER_LIMITS
        require(config.get("reader_limits") == READER_LIMITS, "registered reader deadlines differ")
    require(plan["schema"] == "cgb.portable-soak-plan.v1" and plan["tokenizer"] == counter.identity, "invalid soak plan/counter")
    prefix = "portable-agent-assurance" if config.get("assurance") else "portable-agent"
    require(plan["profile"] == (prefix + "-pilot.v1" if config["pilot_seconds"] else prefix + "-" + str(config["hours"]) + "h.v1"), "pilot and endurance profiles cannot be relabeled")
    require(digest(encoded(plan["registered_profile"])) == plan["registered_profile_sha256"]
            and plan["registered_profile"]["seconds_per_engine"] == config["hours"] * 3600,
            "registered duration profile changed")
    require(all(config[k] == v for k, v in SCHEDULE.items()), "portable schedule changed")
    require(config["hours"] in (1, 24) and config["seconds"] == (config["pilot_seconds"] or config["hours"] * 3600), "duration registration changed")
    require(plan["expected_rounds_per_engine"] == math.ceil(config["seconds"] / config["cadence_seconds"]), "registered cycle denominator changed")
    require(plan["expected_model_waves_per_engine"] == (math.ceil(config["seconds"] / config["model_interval_seconds"]) if config["reader"] else 0), "registered model denominator changed")
    reader = validate_reader(config["reader"]) if config["reader"] else None
    slots, queries, model, captures, resources, controls, ended, restarts, cleanup = {}, {}, {}, {}, defaultdict(list), defaultdict(list), {}, defaultdict(list), {}
    sources, last_sequence, began = {}, 0, {}
    protocols = {} if partial else {e: query_records(root, e) for e in config["engines"]}
    priors, assurance_events = {}, []
    for row in rows(Path(root) / "observations.jsonl.gz", partial):
        last_sequence += 1
        require(row["seq"] == last_sequence and row["engine"] in config["engines"], "unregistered/discontinuous soak journal")
        key, kind = row["engine"], row["kind"]
        if kind == "start":
            require(key not in began, "duplicate engine start")
            began[key] = row
        elif kind == "round":
            slot, number = row["slot"], row["phase"]
            require(type(slot) is int and 0 <= slot < plan["expected_rounds_per_engine"] and (key, slot) not in slots, "duplicate/unregistered soak slot")
            require(type(number) is int and number >= 0 and int(row["elapsed"] // config["mutation_seconds"]) == number,
                    "round source phase differs from its schedule")
            require(set(row["actors"]) == {str(i) for i in range(12)}, "missing actor in registered round")
            slots[key, slot] = True
            if number not in sources:
                sources[number] = phase(number)
            source, public, _ = sources[number]
            for actor, value in row["actors"].items():
                actor = int(actor)
                response = value["response"]
                if not partial and value["request_id"] is not None:
                    binding = protocols[key].get((value["generation"], actor, value["request_id"]))
                    require(binding == (task_payload(source, actor, "agent-" + str(actor)), digest(encoded(response))),
                            "soak observation differs from original query protocol")
                checked = checked_context(source, public, actor, response, counter, bool(config.get("assurance")))
                require(type(value["query_ms"]) in (int, float) and math.isfinite(value["query_ms"]) and value["query_ms"] >= 0, "invalid query time")
                queries[key, slot, actor] = {**checked, "query_ms": value["query_ms"]}
                if config.get("assurance"):
                    from .test_packs.grading import engine_tokens
                    queries[key, slot, actor]["engine_model_tokens"] = engine_tokens(response, [])
                captures[key, slot, actor] = checked
        elif kind == "model":
            wave, actor, number = row["wave"], row["actor"], row["phase"]
            require(reader is not None and type(wave) is int and 0 <= wave < plan["expected_model_waves_per_engine"]
                    and actor // 3 == wave % 4 and (key, wave, actor) not in model, "unregistered/duplicate model call")
            source, public, targets = sources[number]
            task = source["tasks"][actor]
            captured = captures.get((key, row["slot"], actor))
            require(captured is not None and row["captured"] == captured, "reader used changed/unregistered context")
            prior = priors.setdefault((key, wave), [])
            require(actor % 3 == len(prior), "agent handoff order differs")
            expected = {"id": digest(encoded([key, wave, actor])), "messages": messages(task, captured["rendered"], prior),
                        "settings": reader["settings"], "seed": (41 + wave) % 2**31, "context_window": reader["context_window"]}
            require(row["request"] == expected, "reader prompt/settings/handoff changed")
            response = row["response"]
            require(response["id"] == expected["id"], "reader response correlation differs")
            target = targets["tasks"][task["id"]]
            graded = grade_target(response.get("text", ""), task["fields"], target, response["status"])
            fresh = int(row["elapsed"] // config["mutation_seconds"]) == number
            require(fresh == row["fresh"], "reader freshness flag differs from recorded schedule")
            for field in ("input_tokens", "output_tokens"):
                require(response.get(field) is None or type(response[field]) is int and response[field] >= 0, "invalid observed model usage")
            model[key, wave, actor] = {"correct": graded["task_success"] and captured["correct"], "fresh": fresh,
                                       "answerable": graded["answerable"], "abstained": graded["abstained"],
                                       "domain": target.get("domain", ("payments", "search", "identity", "release")[wave % 4]),
                                       "context_tokens": captured["tokens"], "input_tokens": response.get("input_tokens"),
                                       "output_tokens": response.get("output_tokens"), "status": response["status"]}
            prior.append(handoff(task, response))
        elif kind == "resource":
            resources[key].append(row)
        elif kind == "assurance":
            require(config.get("assurance") is True, "unregistered assurance overlay")
            assurance_events.append(row)
        elif kind == "control":
            controls[key].append(row["acknowledgements"] == ["ok", "ok"] and row["response"]["status"] == "refused")
        elif kind == "restart":
            restarts[key].append(all(v["clean"] for v in row["outcomes"]))
        elif kind == "cleanup":
            cleanup[key] = len(row["outcomes"]) == 12 and all(v["clean"] for v in row["outcomes"])
        elif kind == "end":
            require(key not in ended, "duplicate engine completion")
            require(key in began, "engine completion lacks start witness")
            begin_clock, end_clock = began[key]["clock"], row["clock"]
            measured = end_clock["monotonic"] - begin_clock["monotonic"]
            continuous = end_clock["continuous"] - begin_clock["continuous"]
            require(measured >= 0 and abs(measured - row["elapsed"]) < 1, "soak elapsed/clock witness differs")
            row = {**row, "clock_continuity": abs(continuous - measured) < 2}
            ended[key] = row
        elif kind not in ("mutation", "missed", "engine_error"):
            raise ValueError("unknown soak journal record")
    result = []
    for key in config["engines"]:
        observed = [v for (e, _, _), v in queries.items() if e == key]
        outputs = [v for (e, _, _), v in model.items() if e == key]
        correct = sum(v["correct"] for v in outputs)
        known = reader is not None and all(v["input_tokens"] is not None and v["output_tokens"] is not None for v in outputs)
        # Missing scheduled calls are failures with no dispatch cost. Any
        # dispatched call with unknown usage makes the cost metric unavailable.
        tokens = sum((v["input_tokens"] or 0) + (v["output_tokens"] or 0) for v in outputs)
        token_cost = metric(tokens, correct, known)
        expected_cycles = plan["expected_rounds_per_engine"] * 12
        expected_models = plan["expected_model_waves_per_engine"] * 3
        workflows = sum(all(model.get((key, wave, wave % 4 * 3 + role), {}).get("correct", False)
                            and model.get((key, wave, wave % 4 * 3 + role), {}).get("fresh", False) for role in range(3))
                        for wave in range(plan["expected_model_waves_per_engine"]))
        end = ended.get(key)
        duration_complete = bool(end and end["reason"] == "duration-complete" and end["elapsed"] >= config["seconds"] and end["clock_continuity"])
        samples = resources[key]
        rss = [sum(s["rss_bytes"] or 0 for s in r["samples"]) for r in samples if all(s["rss_bytes"] is not None for s in r["samples"])]
        expected_controls = len(range(config["control_seconds"], config["seconds"], config["control_seconds"]))
        result.append({"engine": key, "version": plan["adapters"][key]["version"], "duration_complete": duration_complete,
            "elapsed_seconds": end["elapsed"] if end else None, "registered_cycles": expected_cycles, "observed_cycles": len(observed),
            "correct_cycles": sum(v["correct"] for v in observed), "cycle_success_percent": 100 * sum(v["correct"] for v in observed) / expected_cycles,
            "unobserved_cycles": expected_cycles - len(observed), "query_ms": quantiles([v["query_ms"] for v in observed]),
            "violations": sum(len(v["violations"]) for v in observed), "context_tokens": sum(v["tokens"] for v in observed),
            "model_attempts": len(outputs), "registered_model_attempts": expected_models, "correct_answers": correct,
            "answer_accuracy_percent": 100 * correct / expected_models if expected_models else None,
            "workflow_completion_percent": 100 * workflows / plan["expected_model_waves_per_engine"] if reader else None,
            "model_tokens_per_correct_answer": token_cost, "tokens_saved_per_correct_answer": None,
            "context_tokens_per_correct_answer": sum(v["context_tokens"] for v in outputs) / correct if correct else None,
            "observed_model_tokens": tokens if known else None, "token_basis": "observed reader input + output tokens; no inference used by built-in retrieval adapters",
            "baseline": config["baseline"], "controls_passed": sum(controls[key]), "controls_expected": expected_controls,
            "restarts_observed": len(restarts[key]), "restarts_clean": sum(restarts[key]), "cleanup_clean": cleanup.get(key, False),
            "sampled_adapter_peak_rss_bytes": max(rss) if rss else None, "resource_samples": len(samples),
            "resource_scope": plan["resource_scope"], "overall_score": None,
            "qualification": "diagnostic-pilot" if config["pilot_seconds"] else "public-development-portable-agent-soak"})
    assurance_result = None
    if config.get("assurance"):
        from .test_packs.soak import reduce
        assurance_result = reduce(plan, root, assurance_events, counter)
        for row in result:
            row["assurance"] = assurance_result["engines"][row["engine"]]
            extra = row["assurance"]
            costs = [v["engine_model_tokens"] for (e, _, _), v in queries.items() if e == row["engine"]]
            engine_total = sum(costs) if all(c is not None for c in costs) else None
            total = row["observed_model_tokens"] + engine_total + extra["total_model_tokens"] if all(v is not None for v in (row["observed_model_tokens"], engine_total, extra["total_model_tokens"])) else None
            correct = sum(v["correct"] and v["fresh"] for (e, _, _), v in model.items() if e == row["engine"]) + extra["safe_correct_answers"]
            row["all_tracks_tokens_per_safe_correct_answer"] = {"metric": "all-tracks-tokens-per-safe-correct.v1", "tokens": total,
                "correct_answers": correct, "value": total/correct if total is not None and correct else None}
            outcome_groups = {True: defaultdict(list), False: defaultdict(list)}
            for wave in range(plan["expected_model_waves_per_engine"]):
                _, _, scheduled_targets = phase(wave * config["model_interval_seconds"] // config["mutation_seconds"])
                for role in range(3):
                    actor = wave % 4 * 3 + role
                    value = model.get((row["engine"], wave, actor))
                    target = scheduled_targets["tasks"]["p" + str(wave * 3) + "/agent-" + str(actor)]
                    answerable = value["answerable"] if value else target["answerable"]
                    outcome_groups[answerable][(wave % 4, role)].append(bool(value and value["correct"]))
            group_scores = [100 * sum(sum(v)/len(v) for v in groups.values())/len(groups) if groups else None for groups in outcome_groups.values()]
            row["balanced_answer_quality"] = sum(group_scores)/2 if all(v is not None for v in group_scores) else None
            row["answer_strata"] = {"answerable": group_scores[0], "abstention": group_scores[1]}
            row["model_executions_ok"] = sum(v["status"] == "ok" for (e, _, _), v in model.items() if e == row["engine"])
    baseline = next(v for v in result if v["engine"] == config["baseline"])["model_tokens_per_correct_answer"]["value"]
    for row in result:
        own = row["model_tokens_per_correct_answer"]["value"]
        row["tokens_saved_per_correct_answer"] = baseline - own if baseline is not None and own is not None else None
    complete = all(r["duration_complete"] for r in result)
    return {"schema": "cgb.portable-soak-result.v1", "profile": plan["profile"], "evidence_class": "public-development",
            "overall_policy": selected_policy["id"] if selected_policy else None,
            "overall_status": "incomplete", "unmeasured_assurance": [] if assurance_result else ["security", "privacy"],
            **({"assurance": assurance_result} if assurance_result else {}),
            "reader": {k: reader[k] for k in ("id", "model", "revision", "backend")} if reader else None,
            "all_durations_complete": complete, "duration_qualified": complete and not bool(config["pilot_seconds"]),
            "comparative_qualified": False, "engines": result,
            "limits": ["One synthetic release scenario; twelve simulated agent lanes are not twelve independent task samples.",
                       "Engine cohort is sequential on one host; it is not an independently controlled ranking.",
                       "Periodic adapter RSS misses transient native process peaks; full pipeline cost is unavailable for external model-backed adapters.",
                       "Portable answer workflow completion does not attest native commit/grant/broker safety."]}
