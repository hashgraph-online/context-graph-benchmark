"""Bounded paired-agent execution. Grading lives in a separate, offline reducer."""

from datetime import datetime, timezone
from pathlib import Path
import secrets
import shutil
import signal
import sys
import time

from ..workspace import atomic_json, inside
from ..contracts import validate
from ..evidence import (contained, file_hash, implementation_identity, seal, snapshot_implementation,
                        verify, verify_implementation_snapshot)
from ..isolation import prepare, probe, select
from ..jsonio import Invalid, digest, encoded, read_json, require, write_json
from ..local_models import MlxClient, http_call, validate_reader, verify_artifacts
from ..platform import descriptors
from ..runner import Channel
from ..tokenization import Counter
from . import grading, registry, workload


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def settings(root, selected, reader_path, packs=None, isolation="auto", baseline=None):
    root = Path(root).absolute()
    require(reader_path is not None, "Security/Privacy execution requires an explicit local reader")
    reader = validate_reader(read_json(inside(root, reader_path)))
    paths = descriptors(root, selected)
    Counter(inside(root, ".cgb/tokenizer"))
    entries = registry.resolve(root, packs)
    require(sum(len(e["manifest"]["cases"]) for e in entries)*len(selected) <= 128, "one run supports at most 128 registered case pairs; split larger experimental studies")
    baseline = baseline or ("networkx" if "networkx" in selected else selected[0])
    require(baseline in selected, "baseline must be a selected engine")
    return {"engines": selected, "reader": reader, "reader_path": reader_path, "packs": entries,
            "isolation": select(isolation), "baseline": baseline,
            "wall_seconds": 600+sum(len(e["manifest"]["cases"])*2*e["manifest"]["limits"]["case_seconds"] for e in entries)*len(selected)}, paths


def freeze_adapters(paths):
    result = []
    for path in paths:
        descriptor = validate("adapter-descriptor", read_json(path))
        descriptor["command"] = [sys.executable if arg == "{python}" else arg for arg in descriptor["command"]]
        executable = Path(descriptor["command"][0])
        sha, size = file_hash(executable)
        descriptor["artifacts"].append({"path": str(executable.absolute()), "sha256": sha, "bytes": size, "role": "runtime"})
        result.append(descriptor)
    return result


def verify_inputs(plan, counter, reader=False):
    require(plan["evaluator_sha256"] == implementation_identity() and plan["tokenizer"] == counter.identity,
            "registered evaluator or tokenizer changed")
    for descriptor in plan["adapters"].values():
        for artifact in descriptor["artifacts"]:
            require(file_hash(artifact["path"]) == (artifact["sha256"], artifact["bytes"]), "registered engine artifact changed")
    if reader:
        verify_artifacts(plan["reader"])


def execute_arm(root, relative, descriptor, pack, case, oracle, arm, reader, counter, exchange, isolation, seed=41, stopped=lambda: False):
    """Reusable episode for one-shot packs and registered endurance overlays.

    Model exchange is supervisor-owned. No engine receives labels, reader paths,
    graders, raw journals, other agents' directories, or a host network socket.
    """
    root = Path(root)
    raw = root / "private" / relative
    work = root / "work" / relative
    raw.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=False)
    identity = "episode-" + digest(encoded(relative))[:24]
    record = {"id": identity, "arm": arm, "stages": [], "channels": [], "error": None}
    channel, metadata, memory = None, None, None
    deadline = time.monotonic() + pack["limits"]["case_seconds"]

    def save():
        atomic_json(raw / "record.json", record)

    def close():
        nonlocal channel
        if channel is None:
            return
        try:
            _, response = channel.call("close", {"namespace": identity})
            channel.close_ack = response["status"] == "ok"
        except (Invalid, OSError, TimeoutError):
            channel.close_ack = False
        metadata["cleanup"] = channel.close()
        metadata["returncode"] = channel.process.returncode
        metadata["phases"] = channel.phases
        channel = None
        save()

    try:
        for index in range(workload.stage_count(case)):
            require(not stopped() and time.monotonic() < deadline, "registered episode stopped or timed out")
            specification = workload.stage(case, oracle, arm, index, memory)
            fresh = index == 0 or specification["restart"]
            if fresh:
                close()
                generation = "generation-" + str(len(record["channels"]))
                logs, directory = raw / generation, work / generation
                logs.mkdir()
                directory.mkdir()
                argv, environment, isolation_spec = prepare(descriptor["command"], directory,
                    root / "tokenizer", descriptor["artifacts"], root / "private", isolation)
                channel = Channel(argv, logs, root / "tokenizer", 30000,
                                  working_directory=directory, launch_environment=environment)
                metadata = {"id": generation, "directory": logs.relative_to(root).as_posix(),
                            "description": None, "isolation": isolation_spec, "pid": channel.process.pid,
                            "cleanup": False, "returncode": None, "phases": []}
                record["channels"].append(metadata)
            item = {"index": index, "channel": metadata["id"], "setup_ids": [], "query_id": None,
                    "query_ms": None, "model_request": None, "model_response": None, "effects": []}
            record["stages"].append(item)
            for operation, payload in workload.operations(identity, specification, fresh):
                request_id = "request-" + str(channel.sequence + 1)
                item["setup_ids"].append(request_id)
                _, response = channel.call(operation, payload)
                if operation == "describe" and response["status"] == "ok":
                    metadata["description"] = response["payload"]
                    require(response["payload"]["engine"] == descriptor["engine"] and response["payload"]["version"] == descriptor["version"], "engine identity differs")
                    require(set(pack["required_capabilities"]) <= set(response["payload"]["capabilities"]), "engine capability unavailable")
                require(response["status"] == "ok", "registered " + operation + " failed: " + response["status"])
            payload = {"namespace": identity, "query": specification["task"]["query"],
                       "scope": specification["task"]["scope"], "as_of": None,
                       "required_ids": specification["task"]["required_ids"],
                       "budget": pack["limits"]["context_tokens"], "timeout_ms": 30000}
            item["query_id"] = "request-" + str(channel.sequence + 1)
            before = time.perf_counter()
            try:
                _, response = channel.call("query", payload)
            finally:
                item["query_ms"] = (time.perf_counter() - before) * 1000
                save()
            if response["status"] == "ok":
                require(time.monotonic() < deadline and not stopped(), "episode deadline before reader dispatch")
                request = {"id": identity + "-" + str(index),
                           "messages": workload.messages(specification, response["payload"]["blocks"]),
                           "settings": reader["settings"], "seed": seed + index, "context_window": reader["context_window"]}
                require(len(encoded(request)) <= pack["limits"]["trace_bytes"], "model request trace limit")
                item["model_request"] = request
                save()  # Retain dispatch even if the model runtime terminates.
                try:
                    item["model_response"] = exchange(request)
                except (Invalid, OSError, TimeoutError, ValueError) as error:
                    item["model_response"] = {"id": request["id"], "status": "error", "text": "",
                        "input_tokens": None, "output_tokens": None, "error": type(error).__name__ + ": " + str(error)}
                require(len(encoded(item["model_response"])) <= pack["limits"]["trace_bytes"], "model response trace limit")
                try:
                    parsed = workload.response(item["model_response"].get("text", ""))
                except Invalid:
                    parsed = None
                if parsed:
                    item["effects"], follow = workload.gateway(parsed["actions"], specification["permissions"], specification["public"])
                    memory = follow if follow is not None else memory
            save()
    except (Invalid, OSError, TimeoutError, ValueError) as error:
        record["error"] = type(error).__name__ + ": " + str(error)
    finally:
        close()
        save()
    return (raw / "record.json").relative_to(root).as_posix()


def _paired_execute_pair(root, relative, descriptor, pack, case, oracle, reader, counter, exchange, isolation, seed=41, stopped=lambda: False):
    return {arm: execute_arm(root, relative + "/" + arm, descriptor, pack, case, oracle, arm,
                            reader, counter, exchange, isolation, seed, stopped) for arm in ("clean", "attack")}


def execute_pair(root, relative, descriptor, pack, case, oracle, reader, counter, exchange, isolation, seed=41, stopped=lambda: False):
    from .drivers import get
    return get(pack["driver"], pack["grader"]).execute(root, relative, descriptor, pack, case, oracle,
        reader, counter, exchange, isolation, seed, stopped)


def evaluate(root, partial=False):
    root = Path(root)
    plan, counter = read_json(root / "plan.json"), Counter(root / "tokenizer")
    require(plan["schema"] == "cgb.assurance-plan.v1" and plan["tokenizer"] == counter.identity, "invalid assurance plan/tokenizer")
    validate_reader(plan["reader"])
    packs = {registry.reference(registry.validate_pack(e["manifest"])): e for e in plan["packs"]}
    require(len(packs) == len(plan["packs"]) and plan["isolation"] in ("macos", "bubblewrap", "process"), "invalid pack/isolation inventory")
    core = {registry.reference(p): p for p in registry.builtin()}
    for ref, entry in packs.items():
        require(type(entry["core"]) is bool and (not entry["core"] or core.get(ref) == entry["manifest"]), "community pack cannot supply a core score")
    oracles, inventory = read_json(root / "private/oracles.json"), read_json(root / "private/inventory.json")
    require(len(inventory) == len({r["id"] for r in inventory}), "duplicate registered pair")
    expected = [(engine, ref, case["id"]) for engine in plan["engines"] for ref, entry in packs.items() for case in entry["manifest"]["cases"]]
    require([(r["engine"], r["pack"], r["case"]) for r in inventory] == expected, "changed pair matrix")
    details, rows = [], []
    for cell in inventory:
        path = root / "private/pairs" / (cell["id"] + ".json")
        if not path.is_file():
            continue
        paths = read_json(path)
        require(set(paths) == {"clean", "attack"}, "invalid arm inventory")
        records = {arm: read_json(contained(root, paths[arm]), 8*1024**2) for arm in paths}
        pack = packs[cell["pack"]]["manifest"]
        case = next(c for c in pack["cases"] if c["id"] == cell["case"])
        for record in records.values():
            for channel in record["channels"]:
                description = channel["description"]
                descriptor = plan["adapters"][cell["engine"]]
                if description:
                    require(description["engine"] == descriptor["engine"] and description["version"] == descriptor["version"], "executed engine identity differs")
                require(channel["isolation"]["mode"] == plan["isolation"], "isolation mode differs")
        from .drivers import get
        graded = get(pack["driver"], pack["grader"]).grade(pack, case, oracles[cell["pack"] + "/" + cell["case"]], records,
                                    root, counter, plan["reader"], plan["seed"])
        details.append({"id": cell["id"], "engine": cell["engine"], "pack": cell["pack"], **graded})
    for engine in plan["engines"]:
        summaries = {ref: grading.reduce_pack(e["manifest"], [r for r in details if r["engine"] == engine and r["pack"] == ref]) for ref, e in packs.items()}
        components = {}
        for component in ("security", "privacy"):
            core = [s for ref, s in summaries.items() if packs[ref]["core"] and s["component"] == component]
            components[component] = sum(s["score"] for s in core) / len(core) if core and all(s["score"] is not None for s in core) else None
        own = [r for r in details if r["engine"] == engine]
        complete = len(own) == sum(len(e["manifest"]["cases"]) for e in packs.values())
        total = sum(r["total_model_tokens"] for r in own) if complete and all(r["total_model_tokens"] is not None for r in own) else None
        correct = sum(r["safe_correct_answers"] for r in own)
        gates = sorted({g for r in own for g in r["gates"]})
        rows.append({"engine": engine, "version": plan["adapters"][engine]["version"], "components": components,
            "packs": summaries, "completed_pairs": len(own), "registered_pairs": sum(len(e["manifest"]["cases"]) for e in packs.values()),
            "complete": complete, "critical_violations": gates, "total_model_tokens": total,
            "safe_correct_answers": correct, "tokens_per_safe_correct_answer": total/correct if total is not None and correct else None,
            "tokens_saved_per_safe_correct_answer": None, "overall_score": None})
    baseline = next(r for r in rows if r["engine"] == plan["baseline"])["tokens_per_safe_correct_answer"]
    for row in rows:
        own = row["tokens_per_safe_correct_answer"]
        row["tokens_saved_per_safe_correct_answer"] = baseline-own if baseline is not None and own is not None else None
    # Display redaction occurs AFTER grading. It cannot earn privacy credit.
    protected = [o["secret"] for o in oracles.values()]
    return grading.redact({"schema": "cgb.assurance-result.v1", "profile": "paired-agent-core-development.v1",
        "evaluator_sha256": plan["evaluator_sha256"], "plan_sha256": digest(encoded(plan)), "evidence_class": "public-development",
        "reader": {k: plan["reader"][k] for k in ("id", "model", "revision", "backend")},
        "isolation": read_json(root / "isolation/probe.json"), "complete": all(r["complete"] for r in rows),
        "baseline": plan["baseline"], "engines": rows, "cases": details, "comparative_qualified": False,
        "limits": ["Four public case-template families per component; external curation and independent reproduction are required.",
            "Reference authorization and mock tools measure the declared system composition, not native engine enforcement.",
            "Leak detection covers exact, case/separator-normalized, half-canary and declared base64/hex forms; semantic covert channels are not covered.",
            "Adapter confinement is probed locally, not independently attested. Standalone pairs do not qualify a 1h/24h macro score."]}, protected)


def benchmark(root, selected, reader_path, packs=None, isolation="auto", baseline=None, output=None):
    root = Path(root).absolute()
    config, paths = settings(root, selected, reader_path, packs, isolation, baseline)
    identity = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    destination = Path(output).absolute() if output else inside(root, "lab/assurance/" + identity)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "private").mkdir(mode=0o700)
    (destination / "private/pairs").mkdir()
    shutil.copytree(inside(root, ".cgb/tokenizer"), destination / "tokenizer")
    plan = {"schema": "cgb.assurance-plan.v1", "engines": selected,
            "adapters": dict(zip(selected, freeze_adapters(paths))), "reader": config["reader"],
            "packs": config["packs"], "isolation": config["isolation"], "seed": 41, "baseline": config["baseline"],
            "tokenizer": Counter(destination / "tokenizer").identity, "evaluator_sha256": implementation_identity(),
            "order": "case-paired; engine rotation by case; clean then attack; fresh model prompt cache on every call"}
    write_json(destination / "plan.json", plan)
    require(snapshot_implementation(destination / "evaluator") == plan["evaluator_sha256"], "evaluator changed during registration")
    oracles = {registry.reference(e["manifest"]) + "/" + c["id"]: workload.new_oracle() for e in plan["packs"] for c in e["manifest"]["cases"]}
    write_json(destination / "private/oracles.json", oracles)
    (destination / "private/oracles.json").chmod(0o600)
    inventory = [{"id": digest(encoded([engine, registry.reference(e["manifest"]), c["id"]]))[:24],
                  "engine": engine, "pack": registry.reference(e["manifest"]), "case": c["id"]}
                 for engine in selected for e in plan["packs"] for c in e["manifest"]["cases"]]
    write_json(destination / "private/inventory.json", inventory)
    state = {"schema": "cgb.assurance-state.v1", "id": destination.name, "name": "Security & Privacy execution packs",
        "status": "running", "started_at": stamp(), "updated_at": stamp(), "engines": selected,
        "packs": [registry.reference(e["manifest"]) for e in plan["packs"]], "reader": {k: plan["reader"][k] for k in ("id", "model", "revision", "backend")},
        "completed": 0, "total": len(inventory), "active_engine": None, "active_case": None, "rows": [], "cases": [],
        "replay": "pending", "comparative_qualified": False, "summary": "Public development paired-agent tests; component scores only."}
    def save():
        state["updated_at"] = stamp()
        atomic_json(destination / "state.json", state)
    save()
    stopped, client, failure = False, None, None
    def stop(signum, frame):
        nonlocal stopped
        stopped = True
    previous = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)} if __import__('threading').current_thread() is __import__('threading').main_thread() else {}
    counter = Counter(destination / "tokenizer")
    try:
        probe(destination / "isolation", destination / "tokenizer", destination / "private", config["isolation"])
        verify_inputs(plan, counter, reader=True)
        reader = plan["reader"]
        if reader["backend"] == "mlx":
            (destination / "private/reader").mkdir()
            client = MlxClient(reader, destination / "private/reader", 120)
            write_json(destination / "reader-runtime.json", client.ready)
        def exchange(request):
            return client.exchange(request) if client else http_call(reader, request, 120)
        groups = [(registry.reference(e["manifest"]), c["id"]) for e in plan["packs"] for c in e["manifest"]["cases"]]
        ordered = sorted(inventory, key=lambda c: (groups.index((c["pack"], c["case"])), (selected.index(c["engine"]) - groups.index((c["pack"], c["case"]))) % len(selected)))
        packs_by_id = {registry.reference(e["manifest"]): e["manifest"] for e in plan["packs"]}
        for cell in ordered:
            if stopped or (destination / "STOP").exists():
                stopped = True
                break
            verify_inputs(plan, counter)
            pack = packs_by_id[cell["pack"]]
            case = next(c for c in pack["cases"] if c["id"] == cell["case"])
            state.update(active_engine=cell["engine"], active_case=case["title"])
            save()
            paths = execute_pair(destination, cell["id"], plan["adapters"][cell["engine"]], pack, case,
                oracles[cell["pack"] + "/" + cell["case"]], reader, counter, exchange, config["isolation"], stopped=lambda: stopped or (destination / "STOP").exists())
            write_json(destination / "private/pairs" / (cell["id"] + ".json"), paths)
            result = evaluate(destination, partial=True)
            state.update(completed=state["completed"] + 1, rows=result["engines"], cases=result["cases"])
            save()
        verify_inputs(plan, counter, reader=True)
    except (Invalid, OSError, TimeoutError, ValueError) as error:
        failure = type(error).__name__ + ": " + str(error)
    finally:
        if client:
            client.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if not (destination / "isolation/probe.json").is_file():
        state.update(status="failed", summary=failure, finished_at=stamp())
        save()
        raise Invalid(failure or "isolation unavailable")
    result = evaluate(destination)
    write_json(destination / "result.json", result)
    state.update(status="failed" if failure else "stopped" if stopped else "completed", summary=failure or state["summary"],
                 active_engine=None, active_case=None, rows=result["engines"], cases=result["cases"],
                 finished_at=stamp(), replay="reduction-checked")
    save()
    seal(destination, "report", disposition="partial" if failure or stopped else "complete")
    receipt = replay(destination)
    return {**state, "path": str(destination), "replay": receipt["replay"]}


def replay(path):
    path = Path(path).absolute()
    original = verify(path)
    require(original["kind"] == "report", "wrong assurance bundle")
    plan = read_json(path / "plan.json")
    require(plan["evaluator_sha256"] == implementation_identity(), "use the matching frozen evaluator environment")
    verify_implementation_snapshot(path / "evaluator", plan["evaluator_sha256"])
    result = evaluate(path)
    require(encoded(result) == (path / "result.json").read_bytes(), "assurance replay differs")
    require(verify(path) == original, "assurance evidence changed during replay")
    return {"replay": "passed", "run": str(path), "complete": result["complete"], "independently_attested": False}
