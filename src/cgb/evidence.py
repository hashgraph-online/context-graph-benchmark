"""Contained immutable evidence files and installed evaluator identity."""

import importlib.metadata
from pathlib import Path, PurePosixPath
import shutil

from .contracts import validate
from .jsonio import digest, encoded, read_json, require, write_json

MAX_ARTIFACT = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024


def file_hash(path):
    import hashlib
    hasher = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            require(size <= MAX_ARTIFACT, "artifact byte limit")
            hasher.update(chunk)
    return hasher.hexdigest(), size


def contained(root, relative):
    require(isinstance(relative, str) and "\\" not in relative, "invalid artifact path")
    parts = PurePosixPath(relative).parts
    require(parts and not PurePosixPath(relative).is_absolute() and all(p not in (".", "..") for p in parts), "artifact path escape")
    require("/".join(parts) == relative, "noncanonical artifact path")
    root = Path(root)
    require(not root.is_symlink(), "symlink evidence root")
    path = root.resolve()
    for part in parts:
        path = path / part
        require(not path.is_symlink(), "symlink artifact component")
    require(path.is_file(), "artifact must be a regular file")
    return path


def binding(root, path, role="data"):
    relative = Path(path).relative_to(Path(root)).as_posix()
    resolved = contained(root, relative)
    sha, size = file_hash(resolved)
    return {"path": relative, "sha256": sha, "bytes": size, "role": role}


def seal(root, kind, disposition="complete"):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "output root must be a real directory")
    artifacts = []
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "symlink in output")
        require(path.is_file() or path.is_dir(), "special file in output")
        if path.is_file():
            artifacts.append(binding(root, path))
    manifest = {"schema": "cgb.manifest.v1", "kind": kind,
                "disposition": disposition, "artifacts": artifacts}
    validate("manifest", manifest)
    write_json(root / "manifest.json", manifest)
    verify(root)
    return manifest


def verify(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "evidence root must be a real directory")
    manifest = validate("manifest", read_json(root / "manifest.json"))
    names, total = set(), 0
    for artifact in manifest["artifacts"]:
        relative = artifact["path"]
        require(relative not in names and relative != "manifest.json", "duplicate/self-bound artifact")
        names.add(relative)
        path = contained(root, relative)
        sha, size = file_hash(path)
        require(size == artifact["bytes"] and sha == artifact["sha256"], "artifact identity mismatch")
        total += size
        require(total <= MAX_TOTAL, "evidence byte limit")
    actual = set()
    for path in root.rglob("*"):
        require(not path.is_symlink(), "symlink in evidence")
        require(path.is_file() or path.is_dir(), "special file in evidence")
        if path.is_file() and path != root / "manifest.json":
            actual.add(path.relative_to(root).as_posix())
    require(actual == names, "unbound/missing evidence file")
    return manifest


def implementation_material():
    root = Path(__file__).parent
    inventory = [{"path": p.relative_to(root).as_posix(), "sha256": file_hash(p)[0]}
                 for p in sorted(root.rglob("*.py"))]
    dependencies = {name: importlib.metadata.version(name) for name in
                    ("jsonschema", "regex", "referencing", "rpds-py", "attrs")}
    return {"code": inventory, "dependencies": dependencies}


def implementation_identity():
    return digest(encoded(implementation_material()))


def snapshot_implementation(destination):
    """Retain evaluator source for review; replay never executes these files."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    material = implementation_material()
    source = Path(__file__).parent
    for item in material["code"]:
        target = destination / "code" / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / item["path"], target)
        require(file_hash(target)[0] == item["sha256"], "evaluator changed during snapshot")
    write_json(destination / "identity.json", material)
    return digest(encoded(material))


def verify_implementation_snapshot(destination, expected):
    material = read_json(Path(destination) / "identity.json")
    require(digest(encoded(material)) == expected, "evaluator snapshot identity differs")
    for item in material["code"]:
        require(file_hash(contained(Path(destination) / "code", item["path"]))[0] == item["sha256"], "evaluator snapshot source differs")
