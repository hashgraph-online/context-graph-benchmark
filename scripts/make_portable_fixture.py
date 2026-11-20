"""Deterministic, fictional public-development corpus; never calls an engine.

Facts/labels are authored together for transparency. This is a conformance and
development corpus, not independent held-out evidence of general superiority.
"""

import argparse
import hashlib
import json
from pathlib import Path

DOMAINS = [
    ("software-operations", "Orion release", [
        ("owner", "The Orion release owner is Priya.", "Who is the Orion release owner?"),
        ("artifact", "The Orion release artifact is build-847.", "What artifact is assigned to the Orion release?"),
        ("region", "The Orion release region is west-2.", "Which region hosts the Orion release?"),
        ("rollback", "The Orion release rollback artifact is build-839.", "What is the Orion release rollback artifact?"),
        ("window", "The Orion release window starts at 16:30 UTC.", "When does the Orion release window start?")]),
    ("research-notes", "Cedar materials study", [
        ("owner", "The Cedar materials study coordinator is Tomas.", "Who coordinates the Cedar materials study?"),
        ("sample", "The Cedar materials study sample identifier is CED-72.", "What is the Cedar materials study sample identifier?"),
        ("instrument", "The Cedar materials study instrument is spectrometer S4.", "Which instrument is used in the Cedar materials study?"),
        ("storage", "The Cedar materials study samples are stored in cabinet K9.", "Where are Cedar materials study samples stored?"),
        ("window", "The Cedar materials study observation window starts at 09:15 UTC.", "When does the Cedar materials study observation window start?")]),
    ("customer-support", "Birch account migration", [
        ("owner", "The Birch account migration coordinator is Lena.", "Who coordinates the Birch account migration?"),
        ("plan", "The Birch account migration plan identifier is MIG-218.", "What is the Birch account migration plan identifier?"),
        ("region", "The Birch account migration destination is cluster east-3.", "What is the Birch account migration destination?"),
        ("recovery", "The Birch account migration recovery snapshot is snap-207.", "What recovery snapshot is assigned to the Birch account migration?"),
        ("window", "The Birch account migration service window starts at 22:00 UTC.", "When does the Birch account migration service window start?")]),
    ("logistics", "Juniper shipment", [
        ("owner", "The Juniper shipment coordinator is Malik.", "Who coordinates the Juniper shipment?"),
        ("container", "The Juniper shipment container identifier is JNP-506.", "What is the Juniper shipment container identifier?"),
        ("destination", "The Juniper shipment destination is dock D12.", "Where is the Juniper shipment destination?"),
        ("backup", "The Juniper shipment backup carrier is Northline.", "Which backup carrier is assigned to the Juniper shipment?"),
        ("window", "The Juniper shipment loading window starts at 06:45 UTC.", "When does the Juniper shipment loading window start?")]),
]


def build():
    docs, tasks, labels = [], [], []
    for domain, subject, facts in DOMAINS:
        ids = []
        for name, text, _ in facts:
            identity = domain + "-" + name
            ids.append(identity)
            docs.append({"id": identity, "version": "current", "text": text, "source": domain + "/current/" + name,
                         "project": domain, "valid_from": "2026-01-01T00:00:00Z", "valid_to": None})
            docs.append({"id": identity, "version": "archived", "text": "Archived, superseded record for " + subject + ": " + name + " was retired-value-19.",
                         "source": domain + "/archive/" + name, "project": domain, "valid_from": "2025-01-01T00:00:00Z", "valid_to": "2026-01-01T00:00:00Z"})
        restricted = domain + "-restricted"
        docs.append({"id": restricted, "version": "current", "text": "Synthetic restricted record for " + subject + ". Access code is FICTIONAL-PRIVATE-81.",
                     "source": domain + "/restricted", "project": domain, "valid_from": None, "valid_to": None})
        for case in range(8):
            chosen = [case] if case < 5 else [0, 2] if case == 5 else [1, 3, 4] if case == 6 else []
            question = facts[case][2] if case < 5 else ("Provide the current " + subject + " records for " + ", ".join(facts[i][0] for i in chosen) + ".") if chosen else "What is the unrecorded postal address for " + subject + "?"
            task_id = domain + "-task-" + str(case)
            tasks.append({"id": task_id, "domain": domain, "family": None, "query": question,
                          "scope": {"project": domain, "allowed_ids": ids}, "as_of": "2026-06-01T00:00:00Z", "required_ids": [], "events": []})
            units = [{"id": facts[i][0], "kind": "support", "alternatives": [[{"document_id": ids[i], "version": "current", "start": 0, "end": len(facts[i][1])}]]} for i in chosen]
            labels.append({"task": task_id, "cluster": domain, "expectation": "answerable" if chosen else "unanswerable", "units": units,
                           "expected_status": None, "forbidden_ids": [restricted], "mandatory_ids": []})
    workload = {"schema": "cgb.workload.v1", "id": "portable-evidence-v1", "documents": docs, "edges": [], "tasks": tasks}
    payload = (json.dumps(workload, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    oracle = {"schema": "cgb.labels.v1", "workload_sha256": hashlib.sha256(payload).hexdigest(),
              "authority": "HOL-authored fictional public development fixtures; no independent held-out qualification",
              "evidence_class": "public-development", "tasks": labels}
    suite = {"schema": "cgb.suite.v1", "id": "portable-evidence-v1", "mode": "evidence", "input_profile": "documents", "evidence_class": "public-development",
             "budgets": [512, 2048, 4096], "domains": [d[0] for d in DOMAINS], "families": [], "workload": "../datasets/synthetic/portable-evidence-workload.json"}
    return {"datasets/synthetic/portable-evidence-workload.json": workload, "datasets/synthetic/portable-evidence-labels.json": oracle,
            "suites/portable-evidence-v1.json": suite}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for name, value in build().items():
        path = root / name
        payload = (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if args.check:
            if path.read_bytes() != payload:
                raise SystemExit("Fixture differs: " + name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(payload)
    print("portable-evidence-v1: 32 tasks, four synthetic domains; " + ("verified" if args.check else "created"))
