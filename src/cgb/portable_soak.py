"""Versioned portable 1h/24h multi-agent simulation through generic JSONL engines.

Twelve persistent adapter processes run four planner/operator/reviewer teams.
Native broker qualifications remain a separate track. No producer pass flags
are trusted by the reducer, and no answer metric is inferred from retrieval.
"""

from concurrent.futures import ThreadPoolExecutor
from collections import deque
from datetime import datetime, timezone
import gzip
import math
from pathlib import Path
import random
import secrets
import shutil
import signal
import sys
import time

from .workspace import atomic_json, inside
from .comparison import stamp
from .contracts import validate
from .evidence import file_hash, implementation_identity, seal, snapshot_implementation, verify
from .jsonio import Invalid, MAX_LINE, decode, digest, encoded, read_json, require, write_json
from .local_models import MlxClient, http_call, validate_reader, verify_artifacts
from .platform import descriptors
from .render import render, verify_context
from .runner import Channel
from .reader_protocol import AGENT_ROLES_FORMAT, messages, handoff
from .resources import Clocks, sample_processes
from .workload import generate, documents, required_closure
from .tokenization import Counter

SCHEDULE = {"agents": 12, "cadence_seconds": 5, "mutation_seconds": 60,
            "restart_seconds": 900, "control_seconds": 120, "model_interval_seconds": 180,
            "budget": 1024, "seed": 41}

READER_LIMITS = {"id": "portable-reader-deadlines.v2", "routine_seconds": 30,
                 "assurance_seconds": 120, "mlx_usage_grace_seconds": 120}


def reader_exchange(reader, client, request, seconds):
    if client is None:
        return http_call(reader, request, seconds)
    return client.exchange(request, timeout=seconds, usage_grace=READER_LIMITS["mlx_usage_grace_seconds"])


def verify_bound_inputs(plan, counter, reader=False):
    """Runtime pre/postflight, separate from offline data-only reduction.

    A changed local implementation or supplied executable must never receive a
    duration result under the identities frozen at registration.
    """
    require(plan["evaluator_sha256"] == implementation_identity(), "soak evaluator changed during registered execution")
    require(counter.identity == plan["tokenizer"], "soak tokenizer identity changed")
    for descriptor in plan["adapters"].values():
        for artifact in descriptor["artifacts"]:
            require(file_hash(artifact["path"]) == (artifact["sha256"], artifact["bytes"]), "bound engine artifact changed during soak")
    if reader and plan["config"]["reader"]:
        verify_artifacts(plan["config"]["reader"])


def validate_settings(root, selected, hours, pilot_seconds=None, reader_path=None, baseline=None, assurance=False, isolation="auto"):
    root = Path(root).absolute()
    require(type(hours) is int and hours in (1, 24), "select a 1-hour or 24-hour soak")
    require(pilot_seconds is None or type(pilot_seconds) is int and 5 <= pilot_seconds <= 120, "pilot must be 5–120 seconds")
    descriptors(root, selected)
    Counter(inside(root, ".cgb/tokenizer"))
    baseline = baseline or ("networkx" if "networkx" in selected else selected[0])
    require(baseline in selected, "baseline must be a selected engine")
    reader = validate_reader(read_json(inside(root, reader_path))) if reader_path is not None else None
    require(type(assurance) is bool, "invalid assurance option")
    extra = {}
    if assurance:
        from .isolation import select
        require(reader is not None, "the Security/Privacy soak overlay requires a local reader")
        extra = {"assurance": True, "assurance_interval_seconds": 300, "isolation": select(isolation)}
    if reader is not None:
        extra["reader_limits"] = dict(READER_LIMITS)
    return {"engines": selected, "hours": hours, "pilot_seconds": pilot_seconds, "seconds": pilot_seconds or hours * 3600,
            "reader_path": reader_path, "reader": reader, "baseline": baseline, **SCHEDULE, **extra}


def phase(number):
    public, targets = generate({"seconds": 1, "mutation_seconds": 1, "phase_offset": number})
    source = public["phases"][0]
    docs = [{**d, "project": d["source"], "valid_from": None, "valid_to": None} for d in documents(source).values()]
    return source, {"schema": "cgb.workload.v1", "id": "portable-soak-phase-" + str(number), "documents": docs,
                    "edges": source["edges"], "tasks": []}, targets


def task_payload(source, actor, namespace):
    task = source["tasks"][actor]
    allowed = sorted(d["id"] for d in documents(source).values() if d["source"] in task["allowed_sources"])
    return {"namespace": namespace, "query": task["query"], "scope": {"project": None, "allowed_ids": allowed},
            "as_of": None, "required_ids": task["required"], "budget": SCHEDULE["budget"], "timeout_ms": 30000}


class CompressedChannel(Channel):
    """Retain original bytes once per stream, compressed for a full day of traffic."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("stderr", "stdout", "journal"):
            getattr(self, name).close()
            setattr(self, name, gzip.open(self.directory / (name + ".gz"), "xb"))


def checked_context(source, public, actor, response, counter, expected_refusal=False):
    request = task_payload(source, actor, "replay")
    task = {"id": source["tasks"][actor]["id"], "scope": request["scope"], "as_of": None, "events": []}
    if response.get("status") != "ok":
        if expected_refusal and response.get("status") == "refused" and required_closure(source, source["tasks"][actor]) is None:
            return {"rendered": "", "tokens": 0, "violations": [], "correct": True}
        return {"rendered": "", "tokens": 0, "violations": ["query-" + response.get("status", "error")], "correct": False}
    result = verify_context(response["payload"]["blocks"], public, task, SCHEDULE["budget"], counter)
    closure = required_closure(source, source["tasks"][actor])
    delivered = {s["document_id"] for s in result["spans"] if s["start"] == 0
                 and s["end"] == len(documents(source)[s["document_id"]]["text"])}
    if closure is None or not closure <= delivered:
        result["violations"].append("missing-required-evidence")
    return {"rendered": result["rendered"], "tokens": result["tokens"],
            "violations": sorted(set(result["violations"])), "correct": not result["violations"]}


def benchmark(root, selected, hours=1, pilot_seconds=None, reader_path=None, baseline=None, output=None, assurance=False, isolation="auto"):
    root = Path(root).absolute()
    config = validate_settings(root, selected, hours, pilot_seconds, reader_path, baseline, assurance, isolation)
    registered_profile = read_json(inside(root, "profiles/portable-agent-" + str(hours) + "h.v1.json"))
    require(registered_profile["seconds_per_engine"] == hours * 3600 and
            all(registered_profile[k] == v for k, v in SCHEDULE.items()), "registered soak profile differs from implementation")
    adapter_paths = descriptors(root, selected)
    adapters = []
    for path in adapter_paths:
        descriptor = validate("adapter-descriptor", read_json(path))
        descriptor["command"] = [sys.executable if v == "{python}" else v for v in descriptor["command"]]
        executable = Path(descriptor["command"][0])
        sha, size = file_hash(executable)
        descriptor["artifacts"] += [{"path": str(executable), "sha256": sha, "bytes": size, "role": "runtime"}]
        adapters.append(descriptor)
    identity = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    destination = Path(output).absolute() if output else inside(root, "lab/portable-soaks/" + identity)
    destination.mkdir(parents=True, exist_ok=False)
    tokenizer = destination / "tokenizer"
    tokenizer.mkdir()
    shutil.copyfile(root / ".cgb/tokenizer/o200k_base.tiktoken", tokenizer / "o200k_base.tiktoken")
    counter = Counter(tokenizer)
    from .overall import DAY_POLICY, HOUR_POLICY, policy
    selected_policy = policy(DAY_POLICY if hours == 24 else HOUR_POLICY)
    plan = {"schema": "cgb.portable-soak-plan.v1", "config": config, "adapters": dict(zip(selected, adapters)),
            "overall_policy": selected_policy, "overall_policy_sha256": digest(encoded(selected_policy)),
            "evaluator_sha256": implementation_identity(), "tokenizer": counter.identity,
            "simulation": "four-team-release-handoff.v1", "profile": "portable-agent-pilot.v1" if pilot_seconds else "portable-agent-" + str(hours) + "h.v1",
            "registered_profile": registered_profile, "registered_profile_sha256": digest(encoded(registered_profile)),
            "evidence_class": "public-development", "expected_rounds_per_engine": math.ceil(config["seconds"] / config["cadence_seconds"]),
            "expected_model_waves_per_engine": math.ceil(config["seconds"] / config["model_interval_seconds"]) if config["reader"] else 0,
            "execution": "sequential-engine-cohort; twelve persistent parallel JSONL agent lanes; native engine lifecycle disclosed separately",
            "prompt_format": AGENT_ROLES_FORMAT, "resource_scope": "periodic adapter-owned PID samples; transient native subprocess peaks may be missed"}
    if assurance:
        from .test_packs import registry, workload as assurance_workload
        from .isolation import probe
        plan["assurance_packs"] = [{"manifest": p, "core": True, "source": "bundled"} for p in registry.builtin()]
        overlay_profile = read_json(inside(root, "profiles/portable-agent-assurance-" + str(hours) + "h.v1.json"))
        require(overlay_profile["assurance_interval_seconds"] == 300 and overlay_profile["packs"] == [registry.reference(e["manifest"]) for e in plan["assurance_packs"]], "registered assurance profile differs")
        plan["assurance_profile"] = overlay_profile
        plan["assurance_profile_sha256"] = digest(encoded(overlay_profile))
        plan["expected_assurance_pairs_per_engine"] = math.ceil(config["seconds"] / 300)
        plan["profile"] = "portable-agent-assurance-pilot.v1" if pilot_seconds else "portable-agent-assurance-" + str(hours) + "h.v1"
        (destination / "private").mkdir(mode=0o700)
        write_json(destination / "private/oracles.json", {registry.reference(e["manifest"]) + "/" + c["id"]: assurance_workload.new_oracle()
            for e in plan["assurance_packs"] for c in e["manifest"]["cases"]})
        (destination / "private/oracles.json").chmod(0o600)
        probe(destination / "isolation", tokenizer, destination / "private", config["isolation"])
    write_json(destination / "plan.json", plan)
    write_json(destination / "profile.json", registered_profile)
    require(snapshot_implementation(destination / "evaluator") == plan["evaluator_sha256"], "soak evaluator changed during registration")
    write_json(destination / "environment.json", {"python": sys.version, "platform": __import__('platform').platform(),
                "label_isolation": "not-certified", "engine_order": selected, "network_sandbox": False})
    state = {"schema": "cgb.portable-soak-state.v1", "id": destination.name,
             "overall_policy": selected_policy["id"], "overall_policy_sha256": plan["overall_policy_sha256"],
             "name": ("Diagnostic pilot" if pilot_seconds else str(hours) + "-hour agent soak"),
             "status": "running", "started_at": stamp(), "updated_at": stamp(), "engines": selected,
             "hours": hours, "pilot": pilot_seconds is not None, "seconds_per_engine": config["seconds"],
             "elapsed_seconds": 0, "active_engine": None, "engine_elapsed_seconds": 0, "rows": [], "replay": "pending",
             "live": {},
             "reader": {k: config["reader"][k] for k in ("id", "model", "revision", "backend")} if config["reader"] else None,
             "summary": "Four simulated teams; twelve persistent adapter lanes per engine. Public development evidence.", "comparative_qualified": False}
    def save():
        state["updated_at"] = stamp()
        atomic_json(destination / "state.json", state)
    save()
    stopped = False
    previous = {}
    def stop_handler(signum, frame):
        nonlocal stopped
        stopped = True
    if __import__('threading').current_thread() is __import__('threading').main_thread():
        previous = {s: signal.signal(s, stop_handler) for s in (signal.SIGINT, signal.SIGTERM)}
    overall_start = time.monotonic()
    clocks = Clocks()
    seq = 0
    with gzip.open(destination / "observations.jsonl.gz", "xb") as journal:
        def record(kind, **value):
            nonlocal seq
            seq += 1
            journal.write(encoded({"seq": seq, "kind": kind, **value}))
            journal.flush()
        try:
            verify_bound_inputs(plan, counter, reader=True)
            for key, descriptor in zip(selected, adapters):
                if stopped or (destination / "STOP").exists():
                    stopped = True
                    break
                state["active_engine"] = key
                state["engine_elapsed_seconds"] = 0
                save()
                channels, generation, active_phase = {}, 0, -1
                live_times = deque(maxlen=4096)
                live = {"attempts": 0, "correct": 0, "violations": 0, "context_tokens": 0, "query_ms_rolling": None}
                state["live"][key] = live
                reader, model_client = config["reader"], None
                executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="cgb-agent")
                engine_dir = destination / "engines" / key
                engine_dir.mkdir(parents=True)
                if reader:
                    verify_artifacts(reader)
                    if reader["backend"] == "mlx":
                        model_dir = engine_dir / "reader"
                        model_dir.mkdir()
                        model_client = MlxClient(reader, model_dir, READER_LIMITS["routine_seconds"])
                def open_lanes():
                    nonlocal channels
                    for actor in range(12):
                        directory = engine_dir / ("generation-" + str(generation)) / ("agent-" + str(actor))
                        directory.mkdir(parents=True)
                        if assurance:
                            from .isolation import prepare
                            work = destination / "work/lanes" / key / str(generation) / str(actor)
                            work.mkdir(parents=True)
                            argv, extra, _ = prepare(descriptor["command"], work, tokenizer, descriptor["artifacts"], destination / "private", config["isolation"])
                            channel = CompressedChannel(argv, directory, tokenizer, 30000, working_directory=work, launch_environment=extra)
                        else:
                            channel = CompressedChannel(descriptor["command"], directory, tokenizer, 30000)
                        channels[actor] = channel
                        _, response = channel.call("describe", {})
                        require(response["status"] == "ok" and response["payload"]["engine"] == descriptor["engine"]
                                and response["payload"]["version"] == descriptor["version"], "engine identity/capability probe failed")
                        _, response = channel.call("reset", {"namespace": "agent-" + str(actor)})
                        require(response["status"] == "ok", "agent reset failed")
                def close_lanes():
                    outcomes = []
                    for actor, channel in list(channels.items()):
                        try:
                            _, response = channel.call("close", {"namespace": "agent-" + str(actor)})
                            channel.close_ack = response["status"] == "ok"
                            clean = channel.close()
                        except (Invalid, OSError, TimeoutError):
                            channel.close_ack = False
                            clean = channel.close()
                        outcomes.append({"actor": actor, "clean": clean})
                    channels.clear()
                    return outcomes
                def query_actor(actor, source):
                    before = time.perf_counter()
                    payload = task_payload(source, actor, "agent-" + str(actor))
                    try:
                        request, response = channels[actor].call("query", payload)
                        error = None
                    except (Invalid, OSError, TimeoutError) as exc:
                        request, response, error = None, {"status": "error", "payload": {}}, str(exc)
                    return {"response": response, "request_id": request["id"] if request else None,
                            "generation": generation, "query_ms": (time.perf_counter() - before) * 1000, "error": error}
                start = None
                try:
                    open_lanes()
                    start = time.monotonic()
                    record("start", engine=key, clock=clocks.sample())
                    slot, next_restart, next_control, wave, assurance_wave = 0, config["restart_seconds"], config["control_seconds"], 0, 0
                    while time.monotonic() - start < config["seconds"]:
                        if stopped or (destination / "STOP").exists():
                            stopped = True
                            break
                        elapsed = time.monotonic() - start
                        requested_phase = int(elapsed // config["mutation_seconds"])
                        if elapsed >= next_restart:
                            record("restart", engine=key, elapsed=elapsed, outcomes=close_lanes())
                            generation += 1
                            open_lanes()
                            active_phase = -1
                            next_restart += config["restart_seconds"]
                        if requested_phase != active_phase:
                            source, public, _ = phase(requested_phase)
                            for actor, channel in channels.items():
                                _, ack = channel.call("ingest", {"documents": public["documents"], "edges": public["edges"]})
                                require(ack["status"] == "ok", "source mutation refused")
                            active_phase = requested_phase
                            record("mutation", engine=key, phase=active_phase, elapsed=elapsed,
                                   workload_sha256=digest(encoded(public)))
                        if elapsed >= next_control:
                            channel = channels[0]
                            denied = {"project": None, "allowed_ids": ["policy"]}
                            _, a = channel.call("set_scope", {"scope": denied, "documents": [], "ids": []})
                            _, b = channel.call("query", task_payload(source, 0, "agent-0"))
                            _, c = channel.call("handoff", {"scope": task_payload(source, 0, "agent-0")["scope"], "documents": [], "ids": []})
                            record("control", engine=key, elapsed=elapsed, acknowledgements=[a["status"], c["status"]], response=b)
                            next_control += config["control_seconds"]
                        due = int(elapsed // config["cadence_seconds"])
                        if slot < due:
                            record("missed", engine=key, first_slot=slot, count=due - slot, elapsed=elapsed)
                            slot = due
                        if slot < plan["expected_rounds_per_engine"]:
                            futures = {a: executor.submit(query_actor, a, source) for a in range(12)}
                            actors = {str(a): f.result() for a, f in futures.items()}
                            for actor, value in actors.items():
                                checked = checked_context(source, public, int(actor), value["response"], counter, assurance)
                                live["attempts"] += 1
                                live["correct"] += int(checked["correct"])
                                live["violations"] += len(checked["violations"])
                                live["context_tokens"] += checked["tokens"]
                                live_times.append(value["query_ms"])
                            from .reader_protocol import quantiles
                            live["query_ms_rolling"] = quantiles(list(live_times))
                            record("round", engine=key, slot=slot, phase=active_phase, elapsed=elapsed,
                                   finished_elapsed=time.monotonic() - start, actors=actors)
                            slot += 1
                            if reader and wave < plan["expected_model_waves_per_engine"] and elapsed >= wave * config["model_interval_seconds"]:
                                prior = []
                                team = wave % 4
                                for actor in range(team * 3, team * 3 + 3):
                                    task = source["tasks"][actor]
                                    captured = checked_context(source, public, actor, actors[str(actor)]["response"], counter, assurance)
                                    request = {"id": digest(encoded([key, wave, actor])), "messages": messages(task, captured["rendered"], prior),
                                               "settings": reader["settings"], "seed": (41 + wave) % 2**31, "context_window": reader["context_window"]}
                                    before = time.monotonic()
                                    try:
                                        response = reader_exchange(reader, model_client, request, READER_LIMITS["routine_seconds"])
                                    except (Invalid, OSError, TimeoutError, ValueError) as exc:
                                        response = {"id": request["id"], "status": "error", "text": "", "input_tokens": None, "output_tokens": None, "error": str(exc)}
                                    record("model", engine=key, wave=wave, actor=actor, phase=active_phase, slot=slot - 1,
                                           request=request, response=response, captured=captured,
                                           elapsed=time.monotonic() - start, milliseconds=(time.monotonic() - before) * 1000,
                                           fresh=int((time.monotonic() - start) // config["mutation_seconds"]) == active_phase)
                                    prior.append(handoff(task, response))
                                wave += 1
                        assurance_elapsed = time.monotonic() - start
                        if assurance and assurance_wave < plan["expected_assurance_pairs_per_engine"] and assurance_elapsed >= assurance_wave * 300 and assurance_elapsed < config["seconds"]:
                            from .test_packs.runtime import execute_pair
                            from .test_packs.registry import reference
                            cases = [(e["manifest"], c) for e in plan["assurance_packs"] for c in e["manifest"]["cases"]]
                            pack, case = cases[assurance_wave % len(cases)]
                            oracle = read_json(destination / "private/oracles.json")[reference(pack) + "/" + case["id"]]
                            def exchange(request):
                                return reader_exchange(reader, model_client, request, READER_LIMITS["assurance_seconds"])
                            paths = execute_pair(destination, "soak/" + key + "/" + str(assurance_wave), descriptor, pack, case,
                                oracle, reader, counter, exchange, config["isolation"], stopped=lambda: stopped or (destination / "STOP").exists())
                            record("assurance", engine=key, wave=assurance_wave, pack=reference(pack), case=case["id"],
                                   records=paths, elapsed=assurance_elapsed, finished_elapsed=time.monotonic() - start)
                            assurance_wave += 1
                        samples = sample_processes({c.process.pid: "adapter-agent-" + str(a) for a, c in channels.items()})
                        record("resource", engine=key, elapsed=time.monotonic() - start, samples=samples, clock=clocks.sample())
                        state.update(engine_elapsed_seconds=min(config["seconds"], time.monotonic() - start), elapsed_seconds=time.monotonic() - overall_start)
                        save()
                        deadline = min(start + slot * config["cadence_seconds"], start + config["seconds"])
                        while time.monotonic() < deadline and not stopped and not (destination / "STOP").exists():
                            time.sleep(min(.2, max(0, deadline - time.monotonic())))
                    elapsed = time.monotonic() - start
                    record("end", engine=key, elapsed=elapsed, reason="stopped" if stopped else "duration-complete", clock=clocks.sample())
                except (Invalid, OSError, TimeoutError, ValueError) as exc:
                    record("engine_error", engine=key, error=type(exc).__name__ + ": " + str(exc), elapsed=time.monotonic() - start if start else 0)
                finally:
                    executor.shutdown(wait=True, cancel_futures=True)
                    record("cleanup", engine=key, outcomes=close_lanes())
                    if model_client:
                        model_client.close()
                journal.flush()
                from .portable_soak_scoring import evaluate
                verify_bound_inputs(plan, Counter(tokenizer))
                # Live summaries reduce the journal so far; final replay uses the
                # sealed complete journal and never invokes an engine or reader.
                state["rows"] = evaluate(plan, destination, counter, partial=True)["engines"]
                if start is not None:
                    state["engine_elapsed_seconds"] = time.monotonic() - start
                save()
            verify_bound_inputs(plan, Counter(tokenizer), reader=True)
            state.update(status="stopped" if stopped else "completed", finished_at=stamp(), active_engine=None,
                         elapsed_seconds=time.monotonic() - overall_start)
            save()
        except BaseException as exc:
            state.update(status="failed", active_engine=None, finished_at=stamp(),
                         summary=type(exc).__name__ + ": " + str(exc))
            save()
            raise
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    from .portable_soak_scoring import evaluate
    result = evaluate(plan, destination, counter)
    try:
        verify_bound_inputs(plan, Counter(tokenizer), reader=True)
    except (Invalid, OSError) as exc:
        state.update(status="failed", active_engine=None, summary=str(exc), finished_at=stamp())
        save()
        raise
    write_json(destination / "result.json", result)
    # The independent replay below re-reads every raw protocol/observation and
    # compares the reduction after sealing. A second immediate reduction here
    # duplicated that work without adding a distinct acceptance check.
    state.update(rows=result["engines"], status="stopped" if stopped else "completed" if result["all_durations_complete"] else "failed",
                 replay="reduction-checked", summary="Portable agent simulation complete; no independent qualification is implied.")
    save()
    # Final state is immutable once sealed. Replay status is a separately retained
    # receipt rather than rewriting a file covered by its own manifest.
    seal(destination, "portable-soak", disposition="partial" if stopped else "complete")
    replay(destination)
    return state


def replay(path):
    path = Path(path).absolute()
    original = verify(path)
    require(original["kind"] == "portable-soak", "wrong portable soak bundle")
    plan = read_json(path / "plan.json")
    require(plan["evaluator_sha256"] == implementation_identity(), "use the matching frozen evaluator environment")
    from .portable_soak_scoring import evaluate
    result = evaluate(plan, path, Counter(path / "tokenizer"))
    require(encoded(result) == (path / "result.json").read_bytes(), "portable soak replay differs")
    require(verify(path) == original, "soak changed during replay")
    return {"replay": "passed", "run": str(path), "duration_qualified": result["duration_qualified"],
            "model_answer_metrics": result["reader"] is not None}
