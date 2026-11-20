"""Optional native graph traversal; ranking/scope/packing stay explicit adapter work."""

import importlib
import importlib.metadata
from pathlib import Path

from .evidence import file_hash
from .jsonio import digest, encoded, read_json, require, write_json

class GraphBackend:
    def __init__(self, library, documents, edges):
        require(library == "networkx", "unknown graph library")
        self.module = importlib.import_module(library)
        self.version = importlib.metadata.version(library)
        links = [(e["from"], e["to"]) for e in edges if e["kind"] in ("requires", "contradicts")]
        identities = sorted(set(documents) | {key for pair in links for key in pair})
        self.admitted = set(documents)
        self.graph = self.module.DiGraph()
        self.graph.add_nodes_from(identities)
        self.graph.add_edges_from(links)

    def closure(self, source):
        if source not in self.admitted:
            return None
        reached = self.module.descendants(self.graph, source) | {source}
        # Keep denied/deleted dependency vertices, so missing evidence cannot be dropped.
        return sorted(reached) if reached <= self.admitted else None


def distribution_identity(library):
    require(library == "networkx", "unknown graph library")
    dist = importlib.metadata.distribution(library)
    files = []
    for relative in sorted(dist.files or [], key=str):
        if "__pycache__" in relative.parts or str(relative).endswith(".pyc"):
            continue
        path = Path(dist.locate_file(relative)).absolute()
        require(path.is_file() and not path.is_symlink(), "library payload is not a regular file")
        sha, size = file_hash(path)
        files.append({"path": str(path), "sha256": sha, "bytes": size})
    require(0 < len(files) <= 10000, "library inventory missing or too large")
    return {"schema": "cgb.graph-library.v1", "library": library,
            "version": dist.version, "files": files,
            "dependencies": {}}


def check_distribution(path, library):
    original = read_json(path)
    require(original["library"] == library and original == distribution_identity(library),
            "graph library payload changed")
    return original


def bind_library(library, descriptor_path, output):
    from .contracts import validate
    descriptor = validate("adapter-descriptor", read_json(descriptor_path))
    require(descriptor["engine"] == library, "descriptor/library mismatch")
    importlib.import_module(library)
    root = Path(output).absolute()
    root.mkdir(parents=True, exist_ok=False)
    identity = distribution_identity(library)
    manifest = root / "library.json"
    write_json(manifest, identity)
    sha, size = file_hash(manifest)
    descriptor["version"] = identity["version"]
    descriptor["command"] += ["--library-manifest", str(manifest)]
    descriptor["artifacts"] += [{"path": str(manifest), "sha256": sha, "bytes": size, "role": "library-manifest"}]
    write_json(root / "adapter.json", descriptor)
    return {"adapter": str(root / "adapter.json"), "library": library,
            "version": identity["version"], "payload_sha256": digest(encoded(identity))}
