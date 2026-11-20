"""Explicit optional engine configuration; never discover credentials."""

from pathlib import Path
import re

from .contracts import validate
from .evidence import file_hash
from .jsonio import require, write_json


def configure_cigar(sdk_root, worker, output):
    sdk_root, worker = Path(sdk_root).resolve(), Path(worker).resolve()
    require((sdk_root / "cigar_sdk/local_runtime.py").is_file() and worker.is_file(), "CIGAR SDK parent/worker unavailable")
    runtime = (sdk_root / "cigar_sdk/local_runtime.py").read_text()
    match = re.search(r'^LOCAL_CONTEXT_CORE_VERSION = "([^"\n]+)"', runtime, re.M)
    require(match is not None, "unknown CIGAR core version")
    artifacts = []
    paths = [worker] + sorted(p for p in (sdk_root / "cigar_sdk").rglob("*") if p.is_file() and p.suffix in (".py", ".pyi", ".json") and "__pycache__" not in p.parts)
    for path in paths:
        sha, size = file_hash(path)
        artifacts.append({"path": str(path), "sha256": sha, "bytes": size, "role": "worker" if path == worker else "sdk"})
    descriptor = {"schema": "cgb.adapter-descriptor.v1", "id": "cigar-local", "engine": "cigar",
                  "version": match.group(1), "command": ["{python}", "-m", "cgb.adapters", "--engine", "cigar", "--sdk-root", str(sdk_root), "--worker", str(worker)],
                  "configuration": "cigar-trusted-view-rebuild.v1", "artifacts": artifacts,
                  "capabilities": ["update", "delete", "set_scope", "handoff"], "deployment": "local"}
    validate("adapter-descriptor", descriptor)
    write_json(output, descriptor)
    return {"descriptor": str(output), "core_version": match.group(1), "bound_files": len(artifacts)}
