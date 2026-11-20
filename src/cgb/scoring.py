"""Data-only scoring and deterministic replay; never invokes an engine."""

from pathlib import Path
import shutil
import zipfile

from .contracts import labels as check_labels, validate
from .evidence import contained, implementation_identity, seal, verify, verify_implementation_snapshot
from .jsonio import MAX_LINE, decode, digest, encoded, read_json, require, write_json, write_new
from .metrics.evidence import covered_units, reduce_rows, span_covered
from .render import verify_context
from .runner import check_plan
from .statistics import paired_performance, paired_recall
from .tokenization import Counter


def evaluate(run_path, label_data):
    root = Path(run_path)
    original_manifest = verify(root)
    require(original_manifest["kind"] == "run" and original_manifest["disposition"] == "complete", "incomplete run")
    plan = check_plan(read_json(root / "plan.json"))
    verify_implementation_snapshot(root / "evaluator", plan["harness_sha256"])
    check_labels(label_data, plan["workload"])
    require(plan["suite"]["evidence_class"] == label_data["evidence_class"], "label evidence class mismatch")
    counter = Counter(root / "tokenizer")
    require(counter.identity == plan["tokenizer"], "counter identity changed")
    tasks = {t["id"]: t for t in plan["workload"]["tasks"]}
    labels = {row["task"]: row for row in label_data["tasks"]}
    attempts, identities = [], set()
    with (root / "attempts.jsonl").open("rb") as stream:
        while line := stream.readline(MAX_LINE + 1):
            require(line.endswith(b"\n") and len(line) <= MAX_LINE, "truncated attempt stream")
            attempt = validate("attempt", decode(line, MAX_LINE))
            require(attempt["cell"]["id"] not in identities, "duplicate primary attempt")
            identities.add(attempt["cell"]["id"])
            attempts.append(attempt)
    require([a["cell"] for a in attempts] == plan["cells"], "incomplete/changed primary matrix")
    rows = []
    for attempt in attempts:
        cell = attempt["cell"]
        task, oracle = tasks[cell["task"]], labels[cell["task"]]
        require(int(attempt["finished_ns"]) >= int(attempt["started_ns"]), "invalid attempt clock")
        observed = {"tokens": 0, "spans": [], "violations": []}
        response = None
        if attempt["response_path"] is not None:
            response = validate("adapter-response", read_json(contained(root, attempt["response_path"])))
            request = validate("adapter-request", read_json(contained(root, attempt["request_path"])))
            expected = {"namespace": cell["id"], "query": task["query"], "scope": task["scope"], "as_of": task["as_of"], "required_ids": task["required_ids"], "budget": cell["budget"], "timeout_ms": plan["timeout_ms"]}
            require(request["operation"] == "query" and request["payload"] == expected and request["id"] == response["id"], "query binding differs")
        if attempt["status"] == "ok":
            require(response is not None and response["status"] == "ok" and attempt["cleanup"] and attempt["returncode"] == 0, "successful attempt lacks successful execution")
            observed = verify_context(response["payload"]["blocks"], plan["workload"], task, cell["budget"], counter)
        require(attempt["status"] != "not_run", "unexecuted primary cell")
        units = covered_units(oracle["units"], observed["spans"])
        count = len(oracle["units"])
        violations = list(observed["violations"])
        supplied = {span["document_id"] for span in observed["spans"]}
        if supplied & set(oracle["forbidden_ids"]):
            violations.append("forbidden-evidence")
        if attempt["status"] == "ok" and not set(oracle["mandatory_ids"]) <= supplied:
            violations.append("missing-mandatory-evidence")
        graph_pass = response is not None and oracle["expected_status"] is not None and attempt["status"] == response["status"] == oracle["expected_status"] and not violations and len(units) == count
        cited = {(span["document_id"], span["version"]) for block in response["payload"].get("blocks", []) for span in block["citations"]} if response is not None and response["status"] == "ok" else set()
        annotated = {(span["document_id"], span["version"]) for unit in oracle["units"] for alternative in unit["alternatives"] for span in alternative if span_covered(span, observed["spans"])}
        rows.append({"task": task["id"], "adapter": cell["adapter"], "budget": cell["budget"],
                     "domain": task["domain"], "family": task["family"], "cluster": oracle["cluster"],
                     "status": attempt["status"], "replicate": cell["replicate"], "phases": attempt["phases"], "covered_units": len(units), "gold_units": count,
                     "recall": len(units) / count if count else None, "expectation": oracle["expectation"],
                     "selected_documents": len(cited), "annotated_documents": len(annotated),
                     "annotation_precision": (len(annotated) / len(cited) if cited else 0) if count else None,
                     "contradiction_units": sum(u["kind"] == "contradiction" for u in oracle["units"]),
                     "covered_contradiction_units": sum(u["kind"] == "contradiction" for u in units),
                     "graph_pass": bool(graph_pass), "tokens": observed["tokens"],
                     "violations": sorted(set(violations)), "query_ms": attempt["query_ms"], "startup_ms": attempt["startup_ms"]})
    summaries = {}
    for adapter in plan["adapters"]:
        summaries[adapter["id"]] = {str(budget): reduce_rows([r for r in rows if r["adapter"] == adapter["id"] and r["budget"] == budget], plan["suite"]) for budget in plan["suite"]["budgets"]}
    comparisons = []
    baseline = plan["adapters"][0]["id"]
    for adapter in plan["adapters"][1:]:
        for budget in plan["suite"]["budgets"]:
            selected_rows = [r for r in rows if r["budget"] == budget]
            comparisons.append({"baseline": baseline, "candidate": adapter["id"], "budget": budget,
                                **paired_recall(selected_rows, baseline, adapter["id"], plan["suite"]["domains"], plan["seed"]),
                                "performance": paired_performance(selected_rows, baseline, adapter["id"], plan["seed"], plan["sampling_unit"])})
    result = {"schema": "cgb.result.v1", "plan_sha256": digest(encoded(plan)), "labels_sha256": digest(encoded(label_data)),
              "evaluator_sha256": implementation_identity(), "suite": plan["suite"], "tokenizer": counter.identity,
              "renderer": plan["renderer"], "complete": True, "evidence_class": plan["suite"]["evidence_class"],
              "comparative_qualified": False, "treatments": plan["adapters"], "adapters": summaries, "comparisons": comparisons, "observations": rows}
    validate("result", result)
    require(verify(root) == original_manifest, "run mutated during scoring")
    return result


def score(run_path, labels_path, output):
    label_data = read_json(labels_path)
    result = evaluate(run_path, label_data)
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "labels.json", label_data)
    write_json(root / "result.json", result)
    from .reports import html
    write_new(root / "report.html", html(result))
    seal(root, "report")
    return {"report": str(root.absolute()), "evidence_class": result["evidence_class"], "adapters": result["adapters"]}


def replay(run_path, report_path):
    original = verify(report_path)
    require(original["kind"] == "report", "wrong report kind")
    result = evaluate(run_path, read_json(Path(report_path) / "labels.json"))
    require(encoded(result) == (Path(report_path) / "result.json").read_bytes(), "report replay differs")
    require(verify(report_path) == original, "report mutated during replay")
    return {"replay": "passed", "result_sha256": digest(encoded(result))}


def export(run_path, report_path, output):
    replay(run_path, report_path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for prefix, root in (("run", Path(run_path)), ("report", Path(report_path))):
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    info = zipfile.ZipInfo(prefix + "/" + path.relative_to(root).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes())
    replay(run_path, report_path)
    return {"export": str(output), "uploaded": False}
