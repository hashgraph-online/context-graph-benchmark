"""Bounded, code-free test-pack discovery and explicit local registration."""

from copy import deepcopy
from importlib.resources import files
from pathlib import Path
import re

from ..workspace import inside
from ..jsonio import digest, encoded, read_json, require, write_json, write_new

SCENARIOS = {
    "source-injection": "security", "tool-injection": "security",
    "memory-poisoning": "security", "action-redirection": "security",
    "scope-separation": "privacy", "revocation": "privacy",
    "never-provided": "privacy", "sink-containment": "privacy",
}
from .drivers import DRIVERS, get as get_driver
GRADERS = {"safe-useful-family-macro.v1"}
CORE_FILES = ("security.v1.json", "privacy.v1.json")
IDENTITY = re.compile(r"[a-z][a-z0-9-]{0,47}/[a-z][a-z0-9-]{0,63}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?")
CASE_FIELDS = {"id", "family", "domain", "title", "scenario", "question", "attack", "carrier"}


def validate_pack(value):
    from ..contracts import validate
    validate("test-pack", value)
    require(type(value) is dict and set(value) == {"schema", "id", "version", "title", "description", "license",
        "authors", "evidence_class", "component", "driver", "grader", "required_capabilities", "families", "limits", "cases"},
        "invalid test-pack members; executable fields and remote schemas are not accepted")
    require(value["schema"] == "cgb.test-pack.v1" and isinstance(value["id"], str)
            and IDENTITY.fullmatch(value["id"]) and isinstance(value["version"], str)
            and VERSION.fullmatch(value["version"]), "invalid pack identity/version")
    require(value["component"] in ("security", "privacy", "experimental")
            and value["evidence_class"] == "public-development", "unsupported pack evidence/component class")
    require(value["driver"] in DRIVERS and value["grader"] in GRADERS, "unreviewed execution driver or grader")
    get_driver(value["driver"], value["grader"])
    for key in ("title", "description", "license"):
        require(isinstance(value[key], str) and 1 <= len(value[key]) <= 4096, "pack metadata required")
    require(type(value["authors"]) is list and 1 <= len(value["authors"]) <= 32, "pack authors required")
    for author in value["authors"]:
        require(type(author) is dict and set(author) == {"name", "affiliation"}
                and all(isinstance(v, str) and 1 <= len(v) <= 256 for v in author.values()), "invalid author disclosure")
    families = value["families"]
    require(type(families) is list and 1 <= len(families) <= 32
            and all(isinstance(f, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", f) for f in families)
            and len(families) == len(set(families)), "invalid family inventory")
    caps = value["required_capabilities"]
    require(type(caps) is list and len(caps) == len(set(caps))
            and all(c in ("update", "delete", "set_scope", "handoff") for c in caps), "unknown adapter capability")
    limits = value["limits"]
    maxima = {"context_tokens": 16384, "model_calls_per_arm": 8, "case_seconds": 300, "trace_bytes": 8*1024**2}
    require(type(limits) is dict and set(limits) == set(maxima), "invalid pack limits")
    require(all(type(limits[k]) is int and 1 <= limits[k] <= maxima[k] for k in maxima), "pack exceeds execution bounds")
    cases = value["cases"]
    require(type(cases) is list and 1 <= len(cases) <= 256, "pack requires 1–256 cases")
    ids = []
    for case in cases:
        require(type(case) is dict and set(case) == CASE_FIELDS, "invalid declarative case members")
        require(all(isinstance(v, str) and len(v) <= 16384 for v in case.values()), "invalid case text")
        require(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", case["id"]) and case["family"] in families
                and case["scenario"] in SCENARIOS, "invalid case identity/family/scenario")
        require(value["component"] == "experimental" or SCENARIOS[case["scenario"]] == value["component"],
                "case belongs to a different score component")
        require(case["carrier"] in ("source", "tool-result", "handoff", "memory"), "unsupported attack carrier")
        require(all(p == "attack_marker" for p in re.findall(r"\{\{([^}]+)\}\}", case["attack"])),
                "only public attacker markers can be substituted into attacks")
        require(case["question"] and case["attack"] and case["title"], "case task/attack/title required")
        from .workload import stage_count
        require(stage_count(case) <= limits["model_calls_per_arm"], "driver exceeds registered model call limit")
        ids.append(case["id"])
    require(len(ids) == len(set(ids)) and {c["family"] for c in cases} == set(families), "duplicate cases or missing family")
    return value


def reference(pack):
    return pack["id"] + "@" + pack["version"]


def builtin():
    result = []
    for name in CORE_FILES:
        from ..jsonio import decode
        pack = validate_pack(decode(files("cgb").joinpath("data", "test-packs", name).read_bytes()))
        result.append(pack)
    return result


def inventory(root):
    entries = [{"manifest": p, "core": True, "source": "bundled"} for p in builtin()]
    errors = []
    known = {reference(e["manifest"]) for e in entries}
    for path in sorted((Path(root) / ".cgb/test-packs").glob("*.json"))[:100]:
        try:
            pack = validate_pack(read_json(inside(root, path.relative_to(root).as_posix()), 1024**2))
            require(reference(pack) not in known and not pack["id"].startswith("hol/"), "core namespace cannot be shadowed")
            known.add(reference(pack))
            entries.append({"manifest": pack, "core": False, "source": path.relative_to(root).as_posix()})
        except (ValueError, OSError, TypeError, KeyError) as error:
            errors.append({"path": path.relative_to(root).as_posix(), "error": str(error)})
    return {"entries": entries, "errors": errors}


def catalogue(root):
    data = inventory(root)
    return {"schema": "cgb.test-pack-catalogue.v1", "packs": [{"reference": reference(e["manifest"]),
        **{k: e["manifest"][k] for k in ("id", "version", "title", "description", "license", "authors", "component", "families", "driver", "grader", "limits")},
        "case_count": len(e["manifest"]["cases"]), "manifest_sha256": digest(encoded(e["manifest"])),
        "core_development": e["core"], "evidence_class": "public-development"} for e in data["entries"]],
        "errors": data["errors"], "discovery_executes_code": False}


def resolve(root, requested=None):
    entries = inventory(root)
    require(not entries["errors"], "invalid installed test-pack registry; inspect test-pack list")
    known = {reference(e["manifest"]): e for e in entries["entries"]}
    requested = [reference(p) for p in builtin()] if requested is None else requested
    require(type(requested) is list and 1 <= len(requested) <= 16 and len(requested) == len(set(requested)), "select distinct test packs")
    require(all(r in known for r in requested), "unknown pack/version; register it explicitly first")
    return [deepcopy(known[r]) for r in requested]


def register(root, path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "pack must be a regular JSON file")
    pack = validate_pack(read_json(path, 1024**2))
    require(not pack["id"].startswith("hol/"), "community packs need their own namespace")
    target = inside(root, ".cgb/test-packs/" + digest(encoded(reference(pack))) + ".json")
    write_json(target, pack)
    return {"registered": reference(pack), "manifest_sha256": digest(encoded(pack)), "executed": False,
            "core_development": False, "path": str(target)}


def scaffold(identity, output):
    require(isinstance(identity, str) and IDENTITY.fullmatch(identity) and not identity.startswith("hol/"), "use your own namespace/name")
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=False)
    pack = deepcopy(builtin()[0])
    pack.update(id=identity, version="0.1.0", title="Community instruction-boundary experiment",
                description="An editable public-development case pair; not part of a core score.", component="experimental",
                authors=[{"name": "Contributor", "affiliation": "Disclose your affiliation"}], families=["instruction-boundary"])
    pack["cases"] = [pack["cases"][0]]
    pack["cases"][0].update(id="example", family="instruction-boundary")
    write_json(directory / "pack.json", validate_pack(pack))
    write_new(directory / "README.md", b"# Community test pack\n\nEdit pack.json, disclose provenance/license/affiliation, validate, run conformance and register explicitly. Experimental packs do not change any core score.\n")
    return {"created": str(directory), "reference": reference(pack), "executed": False}
