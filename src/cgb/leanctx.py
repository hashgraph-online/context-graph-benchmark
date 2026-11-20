"""Optional released LeanCTX CLI; native ranking, explicit reference composition.

No engine code is vendored. No labels, model calls, shell integration, global
setup or silent reference-ranker fallback are used.
"""

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .adapters import Engine, CAPABILITIES
from .contracts import validate
from .evidence import file_hash
from .jsonio import MAX_LINE, Invalid, decode, encoded, require, write_json
from .tokenization import Counter


def environment(directory):
    directory = Path(directory).absolute()
    result = {k: os.environ[k] for k in ("PATH", "LANG", "SYSTEMROOT", "WINDIR") if k in os.environ}
    result.update({"LEAN_CTX_" + name.upper() + "_DIR": str(directory / name)
                   for name in ("data", "config", "state", "cache")})
    result.update(LEAN_CTX_TELEMETRY="off", DO_NOT_TRACK="1", CI="1")
    return result


def version(executable, directory):
    result = subprocess.run([str(executable), "--version"], env=environment(directory), cwd=directory,
                            capture_output=True, timeout=20, check=False)
    require(result.returncode == 0, "LeanCTX version probe failed")
    match = re.fullmatch(r"lean-ctx ([0-9]+\.[0-9]+\.[0-9]+)(?: [^\n]*)?\n?", result.stdout.decode())
    require(match is not None, "unrecognized LeanCTX version response")
    return match.group(1)


def configure(executable, output):
    import tempfile
    executable = Path(executable).resolve()
    require(executable.is_file() and os.access(executable, os.X_OK), "LeanCTX executable unavailable")
    with tempfile.TemporaryDirectory(prefix="cgb-leanctx-probe-") as directory:
        observed = version(executable, directory)
    sha, size = file_hash(executable)
    descriptor = {"schema": "cgb.adapter-descriptor.v1", "id": "lean-ctx-local", "engine": "lean-ctx",
                  "version": observed, "command": ["{python}", "-m", "cgb.leanctx", "--executable", str(executable),
                  "--sha256", sha, "--version", observed], "configuration": "leanctx-native-bm25-reference-closure.v1",
                  "artifacts": [{"path": str(executable), "sha256": sha, "bytes": size, "role": "runtime"}],
                  "capabilities": CAPABILITIES, "deployment": "local"}
    validate("adapter-descriptor", descriptor)
    write_json(output, descriptor)
    return {"descriptor": str(output), "engine_version": observed, "executable_sha256": sha}


def ranked_ids(text, mapping):
    """Only native result headers map to known opaque fixture filenames."""
    require(isinstance(text, str) and len(text.encode()) <= MAX_LINE, "LeanCTX output limit")
    if not text.startswith("Semantic search (BM25):"):
        raise Invalid("LeanCTX BM25 unavailable or output contract changed; no fallback")
    result = []
    for match in re.finditer(r"^--- Result [0-9]+ \(score: [^\n]+\) ---\n(doc-[a-f0-9]{64}\.md) :: ", text, re.M):
        name = match.group(1)
        require(name in mapping, "LeanCTX returned a source outside the staged corpus")
        if mapping[name] not in result:
            result.append(mapping[name])
    # An advertised nonempty result must never silently turn into an empty rank.
    header = re.search(r"\(([0-9]+) results? from ", text.splitlines()[0])
    require(header is not None and (int(header.group(1)) == 0 or result), "LeanCTX source mapping failed")
    return result


class LeanEngine(Engine):
    def __init__(self, counter, executable, sha256, expected_version, directory):
        super().__init__("lean-ctx", counter)
        self.executable, self.sha256, self.expected_version = Path(executable), sha256, expected_version
        self.directory = Path(directory).absolute()
        self.calls = 0
        self.native_log = gzip.open(self.directory / "leanctx-native.jsonl.gz", "xb")

    def check(self):
        require(file_hash(self.executable)[0] == self.sha256, "LeanCTX executable changed")

    def ranking(self, documents, query):
        self.check()
        self.calls += 1
        with tempfile.TemporaryDirectory(prefix="leanctx-query-", dir=self.directory) as temporary:
            work = Path(temporary)
            project = work / "project"
            project.mkdir()
            mapping = {}
            for identity, document in sorted(documents.items()):
                name = "doc-" + hashlib.sha256(identity.encode()).hexdigest() + ".md"
                mapping[name] = identity
                (project / name).write_text(document["text"], encoding="utf-8")
            arguments = {"action": "semantic", "query": query, "mode": "bm25", "top_k": 1000}
            from .leanctx_process import invoke
            try:
                text = invoke(self.executable, project, arguments, work, environment(work), 30)
                return ranked_ids(text, mapping)
            finally:
                # Keep native output, not disposable generated indexes/caches.
                self.native_log.write(encoded({"call": self.calls, "arguments": arguments, "sources": mapping,
                    "stdout": (work / "native-stdout.log").read_text() if (work / "native-stdout.log").is_file() else None,
                    "stderr": (work / "native-stderr.log").read_text() if (work / "native-stderr.log").is_file() else None}))
                self.native_log.flush()

    def handle(self, operation, payload):
        if operation == "describe":
            self.check()
            require(version(self.executable, self.directory) == self.expected_version, "LeanCTX version changed")
            return "ok", {"engine": "lean-ctx", "version": self.expected_version, "capabilities": CAPABILITIES,
                          "boundary": "trusted-host-adapter",
                          "preprocessing": "native CLI BM25 file search; trusted scope/time filtering, Python relationship closure and canonical full-source packing; fresh project/index per query; no native broker claims"}, None
        if operation == "close":
            self.check()
            self.native_log.close()
        return super().handle(operation, payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    engine = LeanEngine(Counter(os.environ["CGB_TOKENIZER"]), args.executable, args.sha256, args.version, Path.cwd())
    while line := sys.stdin.buffer.readline(MAX_LINE + 1):
        require(line.endswith(b"\n") and len(line) <= MAX_LINE, "invalid request framing")
        request = validate("adapter-request", decode(line, MAX_LINE))
        try:
            status, payload, error = engine.handle(request["operation"], request["payload"])
        except (Invalid, OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            print(str(exc), file=sys.stderr, flush=True)
            status, payload, error = "error", {}, "leanctx-engine-error"
        sys.stdout.buffer.write(encoded({"schema": "cgb.adapter-response.v1", "id": request["id"],
                                       "status": status, "payload": payload, "error": error}))
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
