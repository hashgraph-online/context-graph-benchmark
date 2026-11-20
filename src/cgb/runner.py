"""Frozen schedules and bounded external process attempts; no scorer labels."""

from pathlib import Path
import os
import platform
import random
import selectors
import shutil
import signal
import subprocess
import sys
import time

from .contracts import unique, validate, workload
from .evidence import file_hash, implementation_identity, seal, snapshot_implementation
from .jsonio import Invalid, MAX_LINE, decode, digest, encoded, read_json, require, write_json, write_new
from .tokenization import Counter


def check_plan(plan):
    validate("plan", plan)
    workload(plan["workload"])
    unique([a["id"] for a in plan["adapters"]], "adapter")
    tasks = {t["id"] for t in plan["workload"]["tasks"]}
    adapters = {a["id"] for a in plan["adapters"]}
    unique([c["id"] for c in plan["cells"]], "cell ID")
    actual = [(c["task"], c["adapter"], c["budget"], c["replicate"]) for c in plan["cells"]]
    unique(actual, "primary cell")
    expected = {(task, adapter, budget, replicate) for task in tasks for adapter in adapters for budget in plan["suite"]["budgets"] for replicate in range(plan["replicates"])}
    require(set(actual) == expected, "incomplete primary matrix")
    require([c["order"] for c in plan["cells"]] == list(range(len(actual))), "changed schedule order")
    require(bool(adapters), "empty adapter inventory")
    require({t["domain"] for t in plan["workload"]["tasks"]} == set(plan["suite"]["domains"]), "suite domain mismatch")
    require({t["family"] for t in plan["workload"]["tasks"] if t["family"] is not None} == set(plan["suite"]["families"]), "suite family mismatch")
    require(plan["suite"]["input_profile"] != "documents" or not plan["workload"]["edges"], "relationships in documents-only treatment")
    require(plan["harness_sha256"] == implementation_identity(), "installed harness differs from frozen plan")
    return plan


def make_plan(suite_path, adapter_paths, tokenizer, seed=0, budgets=None, timeout_ms=5000, replicates=1, sampling_unit="task-cluster"):
    suite_path = Path(suite_path)
    suite = validate("suite", read_json(suite_path))
    public = workload(read_json(suite_path.parent / suite["workload"]))
    if budgets:
        suite["budgets"] = budgets
    adapters = []
    for path in adapter_paths:
        adapter = validate("adapter-descriptor", read_json(path))
        adapter["command"] = [sys.executable if arg == "{python}" else arg for arg in adapter["command"]]
        # Freeze the actual executable bytes, including a venv interpreter target.
        executable = Path(adapter["command"][0])
        require(executable.is_file(), "adapter executable unavailable")
        sha, size = file_hash(executable)
        adapter["artifacts"] = list(adapter["artifacts"]) + [{"path": str(executable.absolute()), "sha256": sha, "bytes": size, "role": "runtime"}]
        adapters.append(adapter)
    cells, order = [], 0
    rng = random.Random(seed)
    ids = [a["id"] for a in adapters]
    rng.shuffle(ids)
    group = 0
    groups = [(task, budget, replicate) for task in public["tasks"] for budget in suite["budgets"] for replicate in range(replicates)]
    for task, budget, replicate in groups:
        rotation = group % max(1, len(ids))
        for adapter in ids[rotation:] + ids[:rotation]:
            cells.append({"id": "cell-" + str(order).zfill(6), "task": task["id"], "adapter": adapter, "budget": budget, "replicate": replicate, "order": order})
            order += 1
        group += 1
    plan = {"schema": "cgb.plan.v1", "suite": suite, "workload": public,
            "adapters": adapters, "tokenizer": Counter(tokenizer).identity,
            "renderer": "cgb.source-spans.v1", "seed": seed, "timeout_ms": timeout_ms,
            "replicates": replicates, "sampling_unit": sampling_unit,
            "cells": cells, "harness_sha256": implementation_identity()}
    return check_plan(plan)


class Channel:
    def __init__(self, command, directory, tokenizer, timeout_ms, *, working_directory=None, launch_environment=None):
        self.directory, self.timeout = Path(directory), timeout_ms / 1000
        self.stderr = (self.directory / "stderr.bin").open("xb")
        self.stdout = (self.directory / "stdout.bin").open("xb")
        self.journal = (self.directory / "journal.jsonl").open("xb")
        environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG") if key in os.environ}
        environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "CGB_TOKENIZER": str(Path(tokenizer).resolve())})
        extra = launch_environment or {}
        require(set(extra) <= {"TMPDIR", "TMP", "TEMP"} and all(isinstance(v, str) for v in extra.values()), "invalid isolated launch environment")
        environment.update(extra)
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            env=environment, cwd=working_directory or self.directory, start_new_session=True)
        except BaseException:
            for stream in (self.stderr, self.stdout, self.journal):
                stream.close()
            raise
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
        self.buffer, self.sequence, self.stderr_bytes = b"", 0, 0
        self.stdout_bytes, self.close_ack, self.phases = 0, False, []
        os.set_blocking(self.process.stdin.fileno(), False)

    def _receive(self, key):
        chunk = os.read(key.fileobj.fileno(), 65536)
        if not chunk:
            self.selector.unregister(key.fileobj)
            if key.data == "stdout":
                raise Invalid("adapter-output-ended")
            return
        if key.data == "stderr":
            self.stderr_bytes += len(chunk)
            require(self.stderr_bytes <= MAX_LINE, "adapter-stderr-limit")
            self.stderr.write(chunk)
        else:
            self.stdout_bytes += len(chunk)
            require(self.stdout_bytes <= 64 * 1024 * 1024, "adapter-total-output-limit")
            self.stdout.write(chunk)
            self.buffer += chunk
            require(len(self.buffer) <= MAX_LINE, "adapter-output-limit")

    def call(self, operation, payload):
        begin = time.monotonic_ns()
        status = "error"
        try:
            result = self._exchange(operation, payload)
            status = result[1]["status"]
            return result
        except TimeoutError:
            status = "timeout"
            raise
        finally:
            self.phases.append({"operation": operation, "milliseconds": (time.monotonic_ns() - begin) / 1e6, "status": status})

    def _exchange(self, operation, payload):
        self.sequence += 1
        request = {"schema": "cgb.adapter-request.v1", "id": "request-" + str(self.sequence), "operation": operation, "payload": payload}
        validate("adapter-request", request)
        self.journal.write(encoded({"direction": "request", "message": request}))
        self.journal.flush()
        deadline = time.monotonic() + self.timeout
        data, sent = encoded(request), 0
        require(len(data) <= MAX_LINE, "adapter-request-limit")
        self.selector.register(self.process.stdin, selectors.EVENT_WRITE, "stdin")
        try:
            while sent < len(data):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("adapter-input-deadline")
                for key, _ in self.selector.select(remaining):
                    if key.data == "stdin":
                        try:
                            sent += os.write(self.process.stdin.fileno(), data[sent:sent + 65536])
                        except BlockingIOError:
                            pass
                        except BrokenPipeError as exc:
                            raise Invalid("adapter-input-ended") from exc
                    else:
                        self._receive(key)
        finally:
            self.selector.unregister(self.process.stdin)
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("adapter-deadline")
            for key, _ in self.selector.select(remaining):
                self._receive(key)
        line, self.buffer = self.buffer.split(b"\n", 1)
        self.journal.write(encoded({"direction": "response-bytes", "hex": line.hex()}))
        self.journal.flush()
        response = validate("adapter-response", decode(line, MAX_LINE))
        require(response["id"] == request["id"], "unexpected-response-id")
        if response["status"] == "ok":
            expected = {"describe": {"engine", "version", "capabilities", "boundary", "preprocessing"},
                        "query": {"blocks", "observed_revision", "usage"}}.get(operation, {"revision", "ready"})
            require(set(response["payload"]) == expected, "response-payload-does-not-match-operation")
        return request, response

    def close(self):
        clean = self.close_ack
        try:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                clean = False
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)
            deadline = time.monotonic() + 1
            while self.selector.get_map() and time.monotonic() < deadline:
                for key, _ in self.selector.select(.1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        self.selector.unregister(key.fileobj)
                    elif key.data == "stderr":
                        self.stderr_bytes += len(chunk)
                        if self.stderr_bytes <= MAX_LINE:
                            self.stderr.write(chunk)
                        else:
                            clean = False
                    else:
                        self.buffer += chunk
                        self.stdout.write(chunk[:MAX_LINE])
                        clean = False
                if len(self.buffer) > MAX_LINE:
                    clean = False
                    break
            if self.selector.get_map():
                clean = False
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            clean = clean and not self.buffer and self.process.returncode == 0
            return clean
        finally:
            self.selector.close()
            for stream in (self.process.stdout, self.process.stderr, self.stderr, self.stdout, self.journal):
                stream.close()


class Declined(Exception):
    def __init__(self, status, error):
        self.status, self.error = status, error


def run(plan, output, tokenizer, progress=None):
    plan = check_plan(plan)
    counter = Counter(tokenizer)
    require(counter.identity == plan["tokenizer"], "tokenizer identity differs")
    root = Path(output).absolute()
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "plan.json", plan)
    require(snapshot_implementation(root / "evaluator") == plan["harness_sha256"], "evaluator changed after plan")
    write_json(root / "environment.json", {"python": sys.version, "platform": platform.platform(), "pid": os.getpid(), "label_isolation": "not-certified", "resource_counters": "unavailable"})
    copied = root / "tokenizer"
    copied.mkdir()
    shutil.copyfile(Path(tokenizer) / "o200k_base.tiktoken", copied / "o200k_base.tiktoken")
    adapters = {a["id"]: a for a in plan["adapters"]}
    tasks = {t["id"]: t for t in plan["workload"]["tasks"]}
    attempts = []
    complete = False
    try:
        for cell in plan["cells"]:
            adapter, task = adapters[cell["adapter"]], tasks[cell["task"]]
            for artifact in adapter["artifacts"]:
                require(file_hash(artifact["path"]) == (artifact["sha256"], artifact["bytes"]), "adapter artifact drift")
            directory = root / "raw" / cell["id"]
            directory.mkdir(parents=True)
            start = time.monotonic_ns()
            attempt = {"schema": "cgb.attempt.v1", "cell": cell, "status": "error", "request_path": None, "response_path": None,
                       "journal_path": str((directory / "journal.jsonl").relative_to(root)), "stderr_path": str((directory / "stderr.bin").relative_to(root)),
                       "started_ns": str(start), "finished_ns": str(start), "query_ms": None, "startup_ms": None,
                       "returncode": None, "pid": None, "cleanup": False, "error": None, "phases": []}
            channel = None
            try:
                channel = Channel(adapter["command"], directory, copied, plan["timeout_ms"])
                attempt["pid"] = channel.process.pid
                _, description = channel.call("describe", {})
                write_json(directory / "description.json", description)
                if description["status"] != "ok":
                    raise Declined(description["status"], description["error"])
                require(description["payload"]["engine"] == adapter["engine"], "engine identity differs from descriptor")
                require(adapter["version"] == "runtime-described" or description["payload"]["version"] == adapter["version"], "engine version differs from descriptor")
                attempt["startup_ms"] = (time.monotonic_ns() - start) / 1e6
                operations = [("reset", {"namespace": cell["id"]}), ("ingest", {"documents": plan["workload"]["documents"], "edges": plan["workload"]["edges"]})]
                operations.extend((e["operation"], {k: v for k, v in e.items() if k != "operation"}) for e in task["events"])
                for operation, payload in operations:
                    _, response = channel.call(operation, payload)
                    if response["status"] != "ok":
                        attempt.update(status=response["status"], error=response["error"])
                        break
                    require(response["payload"].get("ready") is True, "asynchronous-readiness-unsupported")
                else:
                    payload = {"namespace": cell["id"], "query": task["query"], "scope": task["scope"], "as_of": task["as_of"], "required_ids": task["required_ids"], "budget": cell["budget"], "timeout_ms": plan["timeout_ms"]}
                    begin = time.monotonic_ns()
                    try:
                        request, response = channel.call("query", payload)
                    finally:
                        attempt["query_ms"] = (time.monotonic_ns() - begin) / 1e6
                    write_json(directory / "query-request.json", request)
                    write_json(directory / "query-response.json", response)
                    attempt.update(status=response["status"], error=response["error"], request_path=str((directory / "query-request.json").relative_to(root)), response_path=str((directory / "query-response.json").relative_to(root)))
                _, closing = channel.call("close", {"namespace": cell["id"]})
                channel.close_ack = closing["status"] == "ok"
            except Declined as exc:
                attempt.update(status=exc.status, error=exc.error)
            except TimeoutError:
                attempt.update(status="timeout", error="adapter-deadline")
            except (Invalid, OSError, ValueError) as exc:
                attempt.update(status="error", error="adapter-failure")
                write_json(directory / "controller-error.json", {"type": type(exc).__name__, "message": str(exc)})
            finally:
                if channel is not None:
                    attempt["cleanup"] = channel.close()
                    attempt["returncode"] = channel.process.returncode
                    attempt["phases"] = channel.phases
                else:
                    for name in ("journal.jsonl", "stderr.bin"):
                        if not (directory / name).exists():
                            write_new(directory / name, b"")
                attempt["finished_ns"] = str(time.monotonic_ns())
                if not attempt["cleanup"] and attempt["status"] == "ok":
                    attempt.update(status="error", error="cleanup-failure")
                validate("attempt", attempt)
                attempts.append(attempt)
                if progress is not None:
                    progress({"type": "attempt.completed", "completed": len(attempts),
                              "total": len(plan["cells"]), "cell": cell, "status": attempt["status"]})
        for adapter in plan["adapters"]:
            for artifact in adapter["artifacts"]:
                require(file_hash(artifact["path"]) == (artifact["sha256"], artifact["bytes"]), "adapter artifact drift after run")
        require(implementation_identity() == plan["harness_sha256"], "evaluator changed during run")
        complete = True
    finally:
        write_new(root / "attempts.jsonl", b"".join(encoded(a) for a in attempts))
        seal(root, "run", "complete" if complete else "partial")
    return {"run": str(root), "attempts": len(attempts), "disposition": "complete" if complete else "partial"}
