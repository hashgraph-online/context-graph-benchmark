"""Author tiny inspectable smoke/conformance inputs; never invent engine metrics."""

from pathlib import Path
import argparse

from cgb.jsonio import digest, encoded, write_json
from cgb.contracts import labels as validate_labels, workload

parser = argparse.ArgumentParser(description="Create authored fixtures in a new directory")
parser.add_argument("--output", type=Path, required=True)
ROOT = parser.parse_args().output.resolve()
ROOT.mkdir(parents=True, exist_ok=False)
scope = {"project": "alpha", "allowed_ids": None}


def doc(identity, text, project="alpha", version="r1", start=None, end=None):
    return {"id": identity, "version": version, "text": text, "source": identity, "project": project, "valid_from": start, "valid_to": end}


def task(identity, query, family=None, events=None, required=None, selected_scope=None, as_of=None, domain="smoke"):
    return {"id": identity, "domain": domain, "family": family, "query": query, "scope": selected_scope or scope,
            "as_of": as_of, "required_ids": required or [], "events": events or []}


def unit(document, identity=None):
    return {"id": identity or document["id"], "kind": "contradiction" if document["id"] == "counter" else "support", "alternatives": [[{"document_id": document["id"], "version": document["version"], "start": 0, "end": len(document["text"])}]]}


def label(public_task, documents, expectation="answerable", status="ok", forbidden=None):
    return {"task": public_task["id"], "cluster": "shared-fixture-corpus", "expectation": expectation,
            "units": [unit(d) for d in documents], "expected_status": status,
            "forbidden_ids": forbidden or [], "mandatory_ids": []}


def event(operation, documents=None, ids=None, active_scope=None):
    return {"operation": operation, "documents": documents or [], "ids": ids or [], "scope": active_scope}


def save(name, documents, edges, tasks, labels, mode, families):
    public = workload({"schema": "cgb.workload.v1", "id": name, "documents": documents, "edges": edges, "tasks": tasks})
    oracle = {"schema": "cgb.labels.v1", "workload_sha256": digest(encoded(public)),
              "authority": "cgb authored conformance fixtures; no independent efficacy claim", "evidence_class": "smoke", "tasks": labels}
    validate_labels(oracle, public)
    write_json(ROOT / "datasets/synthetic" / (name + "-workload.json"), public)
    write_json(ROOT / "datasets/synthetic" / (name + "-labels.json"), oracle)
    suite = {"schema": "cgb.suite.v1", "id": name + "-v1", "workload": "../datasets/synthetic/" + name + "-workload.json",
             "mode": mode, "input_profile": "supplied-relationships" if mode == "graph" else "documents",
             "domains": sorted({t["domain"] for t in tasks}), "families": families,
             "budgets": [512, 2048, 4096], "evidence_class": "smoke"}
    write_json(ROOT / "suites" / (name + "-v1.json"), suite)


policy = doc("retry-policy", "Retry once. Preserve the operation identifier.")
owner = doc("owner-record", '{"owner":"Mina","limit":1}')
unicode = doc("launch", "東京 🚀 launch on Tuesday.")
distractor = doc("archive", "Archived retry policy: retries are under review.")
beta = doc("beta-policy", "Retry 99 times. Beta has a different owner.", project="beta")
tasks = [task("retry", "retry operation identifier"), task("owner", "owner limit"), task("unicode", "launch Tuesday"), task("unknown", "Atlantis population census")]
labels = [label(tasks[0], [policy]), label(tasks[1], [owner]), label(tasks[2], [unicode]), label(tasks[3], [], "unanswerable")]
save("smoke", [policy, owner, unicode, distractor, beta], [], tasks, labels, "evidence", [])

root = doc("action", "Root action requires its dependency and counterevidence.")
dependency = doc("dependency", "Dependency: retain the operation identity.")
counter = doc("counter", "Counterevidence: this operation may be denied.")
old = doc("changing", "Revision one: retry three times.", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z")
new = doc("changing", "Revision two: retry once.", version="r2", start="2026-01-02T00:00:00Z")
safe = doc("safe", "Safe policy: approved for alpha.")
secret = doc("secret", "Secret policy: alpha private.")
families = ["relationship-closure", "source-changes", "project-switch", "agent-handoff", "temporal-truth", "policy-boundary", "catalog-mutation"]
graph_tasks, graph_labels = [], []


def add(family, suffix, documents, **kwargs):
    expectation = kwargs.pop("expectation", "answerable")
    status = kwargs.pop("status", "ok")
    forbidden = kwargs.pop("forbidden", None)
    current = task(family + "-" + suffix, "policy owner retry action", family, domain="graph", **kwargs)
    graph_tasks.append(current)
    graph_labels.append(label(current, documents, expectation, status, forbidden))


add(families[0], "closure", [root, dependency, counter], required=["action"])
add(families[0], "denied-dependency", [], required=["action"], selected_scope={"project": "alpha", "allowed_ids": ["action"]}, expectation="refuse", status="refused")
add(families[1], "updated", [new], events=[event("update", [new])], required=["changing"])
add(families[1], "withdrawn", [], events=[event("delete", ids=["changing"])], required=["changing"], expectation="refuse", status="refused")
add(families[2], "alpha", [safe], selected_scope={"project": "alpha", "allowed_ids": ["safe"]}, required=["safe"], forbidden=["beta-policy"])
add(families[2], "wrong-project", [], selected_scope={"project": "beta", "allowed_ids": None}, required=["safe"], expectation="refuse", status="refused")
add(families[3], "new-actor", [beta], events=[event("handoff", active_scope={"project": "beta", "allowed_ids": None})], selected_scope={"project": "beta", "allowed_ids": None}, required=["beta-policy"], forbidden=["safe"])
add(families[3], "old-authority", [], events=[event("handoff", active_scope={"project": "beta", "allowed_ids": None})], required=["safe"], expectation="refuse", status="refused")
add(families[4], "historical", [old], events=[event("update", [new])], as_of="2026-01-01T12:00:00Z", required=["changing"])
add(families[4], "before-validity", [], as_of="2025-12-31T00:00:00Z", required=["changing"], expectation="refuse", status="refused")
add(families[5], "permitted", [safe], events=[event("set_scope", active_scope={"project": "alpha", "allowed_ids": ["safe"]})], required=["safe"], forbidden=["secret"])
add(families[5], "revoked", [], events=[event("set_scope", active_scope={"project": "alpha", "allowed_ids": ["safe"]})], required=["secret"], expectation="refuse", status="refused")
add(families[6], "unaffected", [safe], events=[event("delete", ids=["secret"])], required=["safe"], forbidden=["secret"])
add(families[6], "deleted", [], events=[event("delete", ids=["safe"])], required=["safe"], expectation="refuse", status="refused")
save("graph-behavior", [root, dependency, counter, old, safe, secret, beta],
     [{"from": "action", "to": "dependency", "kind": "requires"}, {"from": "action", "to": "counter", "kind": "contradicts"}],
     graph_tasks, graph_labels, "graph", families)

for engine in ["empty", "recent-window", "bm25", "sqlite-fts5"]:
    write_json(ROOT / "adapters" / (engine + ".json"), {"schema": "cgb.adapter-descriptor.v1", "id": engine, "engine": engine,
               "version": "runtime-described" if engine == "sqlite-fts5" else "1", "command": ["{python}", "-m", "cgb.adapters", "--engine", engine], "configuration": engine + ".v1",
               "artifacts": [], "capabilities": ["update", "delete", "set_scope", "handoff"], "deployment": "local"})
