"""Neutral engine inventory and derived platform state. No model/engine imports at startup."""

from datetime import datetime, timezone
import importlib.metadata
import importlib.util
from importlib.resources import files
import math
from pathlib import Path
import re

from .workspace import inside
from . import __version__
from .contracts import validate
from .jsonio import Invalid, decode, read_json, require, write_json

ENGINES = [
    {"id": "cigar", "name": "CIGAR", "category": "Context engine", "color": "#95abff",
     "description": "Native context selection through an explicitly bound SDK and worker.",
     "composition": "Adapter performs trusted authorization/time filtering; SDK profile rebuilds its native view per query.",
     "setup": "docs/engines/cigar.md", "source": "https://github.com/hashgraph-online/hol-cigar"},
    {"id": "networkx", "name": "NetworkX", "category": "Graph substrate + reference adapter", "color": "#4ed6b1",
     "description": "Reference BM25 retrieval and packing, using NetworkX for dependency traversal.",
     "composition": "Scope, time, ranking and packing live in the reference adapter; graph is rebuilt per query.",
     "setup": "python -m pip install -e '.[graphs]'", "source": "https://networkx.org"},
    {"id": "lean-ctx", "name": "LeanCTX", "category": "Context engine + reference composition", "color": "#e5a0ef",
     "description": "Released lean-ctx CLI BM25 file search with explicit reference graph closure and packing.",
     "composition": "Native file-search ranking; reference scope/time/relationship closure and full-source packing. CLI/index startup included.",
     "setup": "docs/engines/lean-ctx.md", "source": "https://github.com/yvgude/lean-ctx"},
]
PROFILES = {
    "evidence": {"id": "evidence", "label": "Evidence retrieval", "suite": "suites/portable-evidence-v1.json",
                 "labels": "datasets/synthetic/portable-evidence-labels.json", "evidence_class": "public-development",
                 "description": "32 documents-only tasks across four fictional domains. Independent source-span recall, current/archived records and scoped negatives."},
    "graph": {"id": "graph", "label": "Graph behavior", "suite": "suites/graph-behavior-v1.json",
              "labels": "datasets/synthetic/graph-behavior-labels.json", "evidence_class": "public-development",
              "description": "Seven relationship, mutation, scope, handoff, temporal and catalog families. Public synthetic development data."},
}


def identity(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value), "invalid engine registry ID")
    return value


def engines(root):
    root = Path(root).absolute()
    result = []
    known = {e["id"]: e for e in ENGINES}
    directory = root / ".cgb/engines"
    extras = [p.stem for p in sorted(directory.glob("*.json")) if p.stem not in known]
    for key in list(known) + extras:
        entry = dict(known.get(key, {"id": key, "name": key, "category": "External JSONL adapter",
                                    "color": "#b8c2dd", "description": "Locally registered external engine.",
                                    "composition": "Consult the adapter descriptor and describe response.", "setup": "docs/adapter-contract.md", "source": ""}))
        entry.update(available=False, version=None, descriptor=None, reason="Bind an installed engine in .cgb/engines/" + key + ".json")
        try:
            identity(key)
            binding = inside(root, ".cgb/engines/" + key + ".json")
            if binding.is_file():
                descriptor = validate("adapter-descriptor", read_json(binding))
                entry.update(descriptor=binding.relative_to(root).as_posix(), version=descriptor["version"])
                from .evidence import file_hash
                require(all(file_hash(Path(a["path"])) == (a["sha256"], a["bytes"]) for a in descriptor["artifacts"]),
                        "Bound engine artifact changed or is unavailable; rebind explicitly")
                if key == "networkx":
                    require(importlib.util.find_spec(key) is not None, "optional graph package unavailable")
                entry.update(available=True, reason=None)
            elif key == "networkx" and importlib.util.find_spec(key) is not None:
                entry.update(available=True, reason=None, version=importlib.metadata.version(key), descriptor="adapters/" + key + ".json")
        except (Invalid, OSError, ValueError, KeyError, TypeError, ModuleNotFoundError) as exc:
            entry.update(available=False, reason=str(exc))
        entry["capabilities"] = {"portable_retrieval": True, "supplied_relationships": True,
                                 "updates_scope_handoff": "trusted adapter composition",
                                 "model_answer_metrics": "requires an explicit reader"}
        result.append(entry)
    return result


def bind_graph(root, library):
    from .graph_backends import bind_library
    import secrets
    root = Path(root).absolute()
    target = inside(root, ".cgb/engines/" + identity(library) + ".json")
    if not target.exists():
        binding = inside(root, ".cgb/bindings/" + library + "-" + secrets.token_hex(6))
        result = bind_library(library, root / "adapters" / (library + ".json"), binding)
        write_json(target, read_json(result["adapter"]))
    return target


def descriptors(root, selected):
    require(type(selected) is list and 1 <= len(selected) <= 8 and len(selected) == len(set(selected)), "select 1–8 distinct engines")
    inventory = {e["id"]: e for e in engines(root)}
    result = []
    for key in selected:
        identity(key)
        require(key in inventory and inventory[key]["available"], "Engine unavailable: " + key + "; bind/install it before execution")
        result.append(bind_graph(root, key) if key == "networkx" else inside(root, inventory[key]["descriptor"]))
    return result


def register(root, key, descriptor_path):
    identity(key)
    descriptor = validate("adapter-descriptor", read_json(descriptor_path))
    path = inside(Path(root).absolute(), ".cgb/engines/" + key + ".json")
    write_json(path, descriptor)
    return {"registered": key, "descriptor": str(path), "executed": False}


def answer_metric_summary(soaks, assurance):
    """Describe recorded cost availability without inventing usage or pooling scores."""
    values = []
    completed = 0
    for runs, field in ((soaks, "model_tokens_per_correct_answer"),
                        (assurance, "tokens_per_safe_correct_answer")):
        for run in runs:
            if run.get("status") != "completed" or not run.get("reader"):
                continue
            completed += 1
            rows = run.get("rows")
            if not isinstance(rows, list):
                continue
            for row in rows:
                value = row.get(field) if isinstance(row, dict) else None
                if isinstance(value, dict):
                    value = value.get("value")
                numeric = type(value) is int or type(value) is float and math.isfinite(value)
                values.append(numeric and value >= 0)
    measured = sum(values)
    return {"status": "available" if values and all(values) else "partial" if measured else "unavailable",
            "completed_runs": completed, "system_results": len(values),
            "measured_results": measured, "missing_results": len(values) - measured,
            "scope": "Routine soak or standalone paired-answer cost; full-study all-track cost is separate."}


def collect(root):
    root = Path(root).absolute()
    comparisons, soaks, errors, readers, overall_studies, assurance, studies = [], [], [], [], [], [], []
    from .local_models import validate_reader
    for path in sorted(root.glob('.cgb/readers/*.json'))[:30]:
        try:
            reader = validate_reader(read_json(inside(root, path.relative_to(root).as_posix())))
            readers.append({k: reader[k] for k in ("id", "model", "revision", "backend")} | {"path": path.relative_to(root).as_posix()})
        except (Invalid, OSError, ValueError, TypeError, KeyError) as exc:
            errors.append({"path": path.relative_to(root).as_posix(), "message": str(exc)})
    for directory, destination, schema in (("comparisons", comparisons, "cgb.platform-comparison.v1"),
                                            ("portable-soaks", soaks, "cgb.portable-soak-state.v1"),
                                            ("assurance", assurance, "cgb.assurance-state.v1"),
                                            ("studies", studies, "cgb.portable-macro-state.v1")):
        for path in sorted(root.glob("lab/" + directory + "/*/state.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            try:
                value = read_json(inside(root, path.relative_to(root).as_posix()), 4 * 1024**2)
                require(value.get("schema") == schema, "unknown platform state")
                destination.append({**value, "path": path.parent.relative_to(root).as_posix()})
            except (Invalid, OSError, ValueError, TypeError) as exc:
                errors.append({"path": path.relative_to(root).as_posix(), "message": str(exc)})
    from .overall import policy
    from .evidence import verify
    for study in studies:
        base = root / study["path"]
        if (base / "manifest.json").is_file():
            try:
                verify(base)
                value = read_json(base / "result.json", 4*1024**2)
                require(value["schema"] == "cgb.overall-result.v4", "unknown portable macro result")
                overall_studies.insert(0, {**value, "path": study["path"], "started_at": study.get("started_at"),
                                          "verification": "Sealed prospective portable study; matching-evaluator replay required."})
            except (Invalid, OSError, ValueError, TypeError, KeyError) as exc:
                errors.append({"path": study["path"], "message": str(exc)})
        for leaf, destination, schema in (("comparison", comparisons, "cgb.platform-comparison.v1"), ("soak", soaks, "cgb.portable-soak-state.v1")):
            if (base / leaf / "state.json").is_file():
                try:
                    value = read_json(base / leaf / "state.json", 4*1024**2)
                    require(value["schema"] == schema, "unknown nested study track")
                    destination.insert(0, {**value, "path": study["path"] + "/" + leaf})
                except (Invalid, OSError, ValueError, TypeError, KeyError) as exc:
                    errors.append({"path": study["path"], "message": str(exc)})
    from .test_packs.registry import catalogue
    for values in (comparisons, soaks, assurance, studies):
        values.sort(key=lambda value: str(value.get("started_at") or ""), reverse=True)
    overall_studies.sort(key=lambda value: (bool(value.get("started_at")), str(value.get("started_at") or "")), reverse=True)
    return {"schema": "cgb.platform.v1", "brand": "HOL: Context Graph Benchmark",
            "version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "engines": engines(root), "profiles": list(PROFILES.values()), "comparisons": comparisons, "soaks": soaks,
            "soak_profiles": [{"id": "portable-agent-1h.v1", "hours": 1, "seconds": 3600},
                              {"id": "portable-agent-24h.v1", "hours": 24, "seconds": 86400}],
            "policy": policy(), "overall_studies": overall_studies,
            "assurance": assurance, "studies": studies, "test_packs": catalogue(root),
            "answer_metrics": answer_metric_summary(soaks, assurance),
            "reference_study": decode(files("cgb").joinpath("data/reference-study.json").read_bytes()),
            "errors": errors, "readers": readers, "comparative_qualified": False,
            "status": "Proposed benchmark · public development fixtures", "visibility": "Local workspace"}
