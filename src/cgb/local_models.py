"""Optional mounted MLX and loopback-only HTTP readers, independent of engines."""

import hashlib
import ipaddress
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .jsonio import Invalid, MAX_LINE, decode, digest, encoded, read_json, require, write_json

SETTINGS = {"temperature", "top_p", "max_tokens", "enable_thinking"}


def model_file_hash(path):
    """External weights have their own bound; evidence-bundle limits stay intact."""
    hasher, size = hashlib.sha256(), 0
    with Path(path).open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            size += len(chunk)
            require(size <= 128 * 1024**3, "model file byte limit")
            hasher.update(chunk)
    return hasher.hexdigest(), size


def endpoint(value):
    require(isinstance(value, str), "local endpoint required")
    url = urllib.parse.urlsplit(value)
    host = url.hostname
    if host == "localhost":
        host = "127.0.0.1"
    try:
        local = ipaddress.ip_address(host).is_loopback
        port = url.port
    except (ValueError, TypeError):
        raise Invalid("use a numeric loopback model endpoint") from None
    require(local and url.scheme in ("http", "https") and not url.username and not url.password
            and not url.query and not url.fragment and url.path in ("", "/", "/v1", "/v1/"),
            "model endpoint must be loopback with no credentials/query/redirect")
    authority = ("[" + host + "]") if ":" in host else host
    return url.scheme + "://" + authority + (":" + str(port) if port else "") + url.path.rstrip("/")


def validate_reader(reader):
    require(type(reader) is dict, "invalid local reader descriptor")
    common = {"schema", "id", "backend", "model", "revision", "context_window", "settings", "artifacts"}
    extra = {"python", "model_path"} if reader.get("backend") == "mlx" else {"base_url"}
    require(type(reader) is dict and set(reader) == common | extra
            and reader["schema"] == "cgb.local-reader.v1", "invalid local reader descriptor")
    require(all(isinstance(reader[k], str) and 0 < len(reader[k]) <= 512 for k in ("id", "model", "revision")),
            "reader model/revision identity required")
    require(reader["backend"] in ("mlx", "ollama", "openai-compatible"), "unsupported local reader backend")
    require(type(reader["context_window"]) is int and 128 <= reader["context_window"] <= 16777216,
            "invalid model context window")
    settings = reader["settings"]
    require(type(settings) is dict and set(settings) == SETTINGS, "invalid decoding settings")
    require(type(settings["temperature"]) in (int, float) and 0 <= settings["temperature"] <= 2
            and type(settings["top_p"]) in (int, float) and 0 < settings["top_p"] <= 1
            and type(settings["max_tokens"]) is int and 1 <= settings["max_tokens"] <= 16384
            and type(settings["enable_thinking"]) is bool, "invalid sampling/output bounds")
    require(type(reader["artifacts"]) is list and len(reader["artifacts"]) <= 1024, "invalid model artifact inventory")
    seen = set()
    for artifact in reader["artifacts"]:
        require(type(artifact) is dict and set(artifact) == {"path", "bytes", "sha256"}
                and Path(artifact["path"]).is_absolute() and artifact["path"] not in seen
                and type(artifact["bytes"]) is int and artifact["bytes"] >= 0
                and isinstance(artifact["sha256"], str) and len(artifact["sha256"]) == 64,
                "invalid model artifact binding")
        seen.add(artifact["path"])
    if reader["backend"] == "mlx":
        require(Path(reader["python"]).is_absolute() and Path(reader["model_path"]).is_absolute()
                and bool(reader["artifacts"]), "mounted MLX model and Python must be explicit absolute paths")
    else:
        endpoint(reader["base_url"])
    return reader


def bind_mlx(model_path, python, output, identity, model, revision, temperature=0, max_tokens=128):
    root = Path(model_path).absolute()
    require(root.is_dir() and not root.is_symlink(), "real mounted model directory required")
    paths = sorted(p for p in root.iterdir() if p.suffix in (".json", ".safetensors", ".jinja", ".txt"))
    require(0 < len(paths) <= 1024 and any(p.suffix == ".safetensors" for p in paths), "local safetensors absent")
    artifacts = []
    for path in paths:
        require(path.is_file() and not path.is_symlink(), "model artifact must be a regular file")
        sha, size = model_file_hash(path)
        artifacts.append({"path": str(path), "bytes": size, "sha256": sha})
    config = read_json(root / "config.json")
    window = config.get("text_config", config).get("max_position_embeddings", 32768)
    result = {"schema": "cgb.local-reader.v1", "id": identity, "backend": "mlx", "model": model,
              "revision": revision, "python": str(Path(python).absolute()), "model_path": str(root),
              "context_window": window, "settings": {"temperature": temperature, "top_p": 1.0,
              "max_tokens": max_tokens, "enable_thinking": False}, "artifacts": artifacts}
    validate_reader(result)
    write_json(output, result)
    return {"reader": str(output), "artifacts": len(artifacts), "bytes": sum(a["bytes"] for a in artifacts),
            "artifact_inventory_sha256": digest(encoded(artifacts))}


def verify_artifacts(reader):
    validate_reader(reader)
    for artifact in reader["artifacts"]:
        path = Path(artifact["path"])
        require(path.is_file() and not path.is_symlink(), "model artifact unavailable")
        sha, size = model_file_hash(path)
        require((sha, size) == (artifact["sha256"], artifact["bytes"]), "model artifact changed")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Invalid("local model redirects are disabled")


def http_json(base_url, path, payload=None, timeout=60):
    base = endpoint(base_url)
    body = encoded(payload) if payload is not None else None
    request = urllib.request.Request(base + path, data=body, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        data = response.read(MAX_LINE + 1)
    return decode(data, MAX_LINE)


def probe(backend, base_url, output):
    require(backend in ("ollama", "openai-compatible"), "select an HTTP backend")
    value = http_json(base_url, "/api/tags" if backend == "ollama" else "/models", timeout=5)
    write_json(output, {"schema": "cgb.local-model-inventory.v1", "backend": backend,
                        "base_url": endpoint(base_url), "response": value})
    return {"inventory": str(output), "response": value}


def http_call(reader, request, timeout):
    settings = request["settings"]
    if reader["backend"] == "ollama":
        payload = {"model": reader["model"], "messages": request["messages"], "stream": False,
                   "think": settings["enable_thinking"], "options": {"seed": request["seed"],
                   "temperature": settings["temperature"], "top_p": settings["top_p"],
                   "num_predict": settings["max_tokens"], "num_ctx": reader["context_window"]}}
        native = http_json(reader["base_url"], "/api/chat", payload, timeout)
        require(native.get("done") is True and native.get("model") == reader["model"], "incomplete/substituted model response")
        text = native["message"]["content"]
        input_tokens, output_tokens = native.get("prompt_eval_count"), native.get("eval_count")
        finish = native.get("done_reason")
    else:
        require(not settings["enable_thinking"], "thinking control is not portable in the compatible HTTP profile")
        payload = {"model": reader["model"], "messages": request["messages"], "stream": False,
                   "temperature": settings["temperature"], "top_p": settings["top_p"],
                   "max_tokens": settings["max_tokens"], "seed": request["seed"]}
        native = http_json(reader["base_url"], "/chat/completions", payload, timeout)
        require(native.get("model") == reader["model"] and len(native["choices"]) == 1, "substituted model/multiple completions")
        choice = native["choices"][0]
        require(not choice["message"].get("tool_calls"), "reader tools are disabled")
        text = choice["message"].get("content")
        usage = native.get("usage") or {}
        input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
        finish = choice.get("finish_reason")
    require(isinstance(text, str), "reader must return full displayed text")
    for value in (input_tokens, output_tokens):
        require(value is None or type(value) is int and value >= 0, "invalid model usage")
    return {"id": request["id"], "status": "ok", "text": text, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "finish_reason": finish, "native": native}


class MlxClient:
    """One owned fixed worker at a time; failed primaries are never retried.

    A failed exchange retires its process and framing state before returning.
    Only a later registered request can create a new generation, within that
    request's original deadline. Decoding and fresh-prompt-cache policy remain
    the fixed worker's responsibility.
    """

    def __init__(self, reader, directory, timeout):
        self.timeout, self.buffer, self.total = timeout, b"", 0
        self._owner_pid, self._closed, self._retired = os.getpid(), False, False
        self._state_lock = threading.Lock()
        self._generation = 0
        self._directory = Path(directory)
        self._argv = [reader["python"], "-I", str(Path(__file__).with_name("local_mlx_worker.py")), reader["model_path"]]
        self.selector, self.process, self.log, self.lifecycle = None, None, None, None
        try:
            self.log = (self._directory / "worker-stderr.log").open("xb")
            self.lifecycle = (self._directory / "worker-lifecycle.jsonl").open("xb")
            self._start(time.monotonic() + timeout)
        except BaseException:
            self.close()
            raise

    def _event(self, event, **fields):
        self.lifecycle.write(encoded({"event": event, "generation": self._generation,
            "pid": self.process.pid if self.process else None, **fields}))
        self.lifecycle.flush()

    def _owner(self):
        require(os.getpid() == self._owner_pid, "local reader belongs to another process")

    def _start(self, deadline):
        self._owner()
        require(not self._closed, "local reader is closed")
        require(self.process is None or self.process.poll() is not None, "previous reader cleanup incomplete")
        require(time.monotonic() < deadline, "local model deadline before recovery")
        self.buffer = b""
        self._generation += 1
        self.selector = selectors.DefaultSelector()
        environment = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT") if key in os.environ}
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                           PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", TOKENIZERS_PARALLELISM="false")
        self.process = None
        try:
            self.process = subprocess.Popen(self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=self._directory, env=environment, start_new_session=True)
            self._event("worker-started")
            for stream, name in ((self.process.stdout, "stdout"), (self.process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                self.selector.register(stream, selectors.EVENT_READ, name)
            os.set_blocking(self.process.stdin.fileno(), False)
            self.ready = self._exchange(None, deadline)
            require(self.ready.get("ready") is True, "MLX worker not ready")
            self._retired = False
            self._event("worker-ready")
        except BaseException:
            self._retired = True
            self._stop_worker(force=True)
            raise

    def exchange(self, request, *, timeout=None, usage_grace=0):
        """Deliver one request. A late native receipt can account for cost only."""
        self._owner()
        require(not self._closed, "local reader is closed")
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        require(0 <= usage_grace <= 120, "invalid native usage grace")
        if not self._state_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise TimeoutError("local reader busy before deadline")
        try:
            # close() sets cancellation before waiting on this lock. It cannot
            # close logs/streams or finish while a new child is being created.
            require(not self._closed, "local reader is closed")
            try:
                if self._retired:
                    self._start(deadline)
                response = self._exchange(request, deadline, usage_grace)
            except BaseException as error:
                self._retire(type(error).__name__)
                raise
            if response.get("usage_source") == "late-native-completion":
                self._retire("TimeoutError", usage_recorded=True)
            return response
        finally:
            self._state_lock.release()

    def _retire(self, error_type, **fields):
        self._retired = True
        try:
            self._event("exchange-failed", error_type=error_type, **fields)
        finally:
            self._stop_worker(force=True)

    def _exchange(self, request, deadline, usage_grace=0):
        data, sent = encoded(request) if request is not None else b"", 0
        require(len(data) <= MAX_LINE, "reader input byte limit")
        if data:
            self.selector.register(self.process.stdin, selectors.EVENT_WRITE, "stdin")
        try:
            while sent < len(data) or b"\n" not in self.buffer:
                require(not self._closed, "local reader is closed")
                # Only a fully dispatched request may wait for native accounting.
                # No second write is issued, even if the first answer is late.
                receive_until = deadline + (usage_grace if data and sent == len(data) else 0)
                remaining = receive_until - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("local model deadline")
                for key, _ in self.selector.select(min(remaining, .25)):
                    if key.data == "stdin":
                        if time.monotonic() >= deadline:
                            raise TimeoutError("local model deadline")
                        try:
                            sent += os.write(key.fileobj.fileno(), data[sent:sent + 65536])
                        except BlockingIOError:
                            continue
                        if sent == len(data):
                            self.selector.unregister(key.fileobj)
                    else:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            self.selector.unregister(key.fileobj)
                            require(key.data != "stdout", "local worker ended")
                            continue
                        self.total += len(chunk)
                        require(self.total <= 64 * 1024 * 1024, "worker output byte limit")
                        if key.data == "stderr":
                            self.log.write(chunk)
                        else:
                            self.buffer += chunk
                            require(len(self.buffer) <= MAX_LINE, "worker frame byte limit")
            received_at = time.monotonic()
            line, self.buffer = self.buffer.split(b"\n", 1)
            require(not self.buffer, "unsolicited local model frame")
            value = decode(line, MAX_LINE)
            require(isinstance(value, dict), "local model response must be an object")
            if request is not None:
                require(value.get("id") == request["id"], "unexpected local model response identity")
                if received_at > deadline:
                    counts = [value.get(k) for k in ("input_tokens", "output_tokens")]
                    if (not usage_grace or received_at > deadline + usage_grace or value.get("status") != "ok"
                            or not all(type(v) is int and v >= 0 for v in counts)):
                        raise TimeoutError("local model deadline without a complete native usage receipt")
                    return {"id": request["id"], "status": "error", "text": "",
                            "input_tokens": counts[0], "output_tokens": counts[1],
                            "finish_reason": "deadline", "error": "local model answer deadline exceeded",
                            "usage_source": "late-native-completion", "seconds_past_deadline": received_at - deadline}
            return value
        finally:
            if data:
                try:
                    self.selector.unregister(self.process.stdin)
                except (KeyError, ValueError):
                    pass

    def _stop_worker(self, force=False):
        self._owner()
        try:
            if self.process is not None:
                if not self.process.stdin.closed:
                    self.process.stdin.close()
                if not force:
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        force = True
                if force and self.process.poll() is None:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.process.wait(timeout=5)
        finally:
            if self.process is not None:
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    stream.close()
            if self.selector is not None:
                self.selector.close()
                self.selector = None
            self.buffer = b""

    def close(self):
        self._owner()
        self._closed = True
        if not self._state_lock.acquire(timeout=8):
            raise TimeoutError("local reader cleanup incomplete; retry close")
        try:
            try:
                self._stop_worker()
            finally:
                for stream in (self.log, self.lifecycle):
                    if stream is not None:
                        stream.close()
        finally:
            self._state_lock.release()
