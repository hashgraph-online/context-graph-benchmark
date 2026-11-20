"""Authoritative v1 JSON Schema definitions, exported by cgb schemas export."""

S = {"type": "string", "maxLength": 1048576}
ID = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"}
N = {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
POS = {**N, "minimum": 1}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
NULL = {"type": "null"}
STATUS = {"enum": ["ok", "abstained", "refused", "unsupported", "error", "timeout", "unknown", "not_run"]}


def ref(name):
    return {"$ref": "#/$defs/" + name}


def arr(items, maximum=100000):
    return {"type": "array", "items": items, "maxItems": maximum}


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def nullable(schema):
    return {"anyOf": [schema, NULL]}


def tagged(name, properties):
    return obj({"schema": {"const": "cgb." + name + ".v1"}, **properties})


SPAN = obj({"document_id": ID, "version": ID, "start": N, "end": POS})
SCOPE = obj({"project": nullable(ID), "allowed_ids": nullable(arr(ID))})
DOCUMENT = obj({"id": ID, "version": ID, "text": S, "source": S,
                "project": ID, "valid_from": nullable(S), "valid_to": nullable(S)})
EDGE = obj({"from": ID, "to": ID,
            "kind": {"enum": ["requires", "supports", "contradicts", "supersedes", "related"]}})
EVENT = obj({"operation": {"enum": ["update", "delete", "set_scope", "handoff"]},
             "documents": arr(ref("document")), "ids": arr(ID), "scope": nullable(ref("scope"))})
TASK = obj({"id": ID, "domain": ID, "family": nullable(ID), "query": S,
            "scope": ref("scope"), "as_of": nullable(S), "required_ids": arr(ID),
            "events": arr(ref("event"), 1000)})
UNIT = obj({"id": ID, "kind": {"enum": ["support", "contradiction"]},
            "alternatives": {**arr({**arr(ref("span"), 128), "minItems": 1}, 128), "minItems": 1}})
LABEL = obj({"task": ID, "cluster": ID,
             "expectation": {"enum": ["answerable", "unanswerable", "unannotated", "refuse"]},
             "units": arr(ref("unit"), 8192),
             "expected_status": nullable({"enum": ["ok", "abstained", "refused"]}), "forbidden_ids": arr(ID),
             "mandatory_ids": arr(ID)})
ARTIFACT = obj({"path": S, "sha256": HASH, "bytes": N, "role": ID})
DESCRIPTOR = tagged("adapter-descriptor", {"id": ID, "engine": ID, "version": S,
    "command": {**arr(S, 64), "minItems": 1}, "configuration": ID,
    "artifacts": arr(ref("artifact"), 256), "capabilities": arr(ID, 64),
    "deployment": {"enum": ["local", "hosted"]}})
SUITE = tagged("suite", {"id": ID, "workload": S, "mode": {"enum": ["evidence", "graph"]},
    "input_profile": {"enum": ["documents", "supplied-relationships"]},
    "domains": {**arr(ID, 32), "minItems": 1, "uniqueItems": True},
    "families": {**arr(ID, 32), "uniqueItems": True},
    "budgets": {**arr(POS, 32), "minItems": 1, "uniqueItems": True},
    "evidence_class": {"enum": ["smoke", "public-development", "held-out"]}})
CELL = obj({"id": ID, "task": ID, "adapter": ID, "budget": POS, "replicate": N, "order": N})
TOKENIZER = obj({"id": ID, "implementation": S, "version": S, "vocabulary_sha256": HASH,
                 "pattern_sha256": HASH, "mode": {"const": "ordinary"}})
DEFS = {
    "span": SPAN, "scope": SCOPE, "document": DOCUMENT, "edge": EDGE,
    "event": EVENT, "task": TASK, "unit": UNIT, "label": LABEL,
    "artifact": ARTIFACT, "cell": CELL, "tokenizer": TOKENIZER,
    "adapter-descriptor": DESCRIPTOR,
    "workload": tagged("workload", {"id": ID, "documents": arr(ref("document")),
                                    "edges": arr(ref("edge")), "tasks": arr(ref("task"), 4096)}),
    "labels": tagged("labels", {"workload_sha256": HASH, "authority": S,
                                "evidence_class": {"enum": ["smoke", "public-development", "held-out"]},
                                "tasks": arr(ref("label"), 4096)}),
    "suite": SUITE,
    "adapter-request": tagged("adapter-request", {"id": ID, "operation": {"enum": ["describe", "reset", "ingest", "query", "close", "update", "delete", "set_scope", "handoff"]}, "payload": {"type": "object"}}),
    "adapter-response": tagged("adapter-response", {"id": ID, "status": {"enum": ["ok", "abstained", "refused", "unsupported", "error"]}, "payload": {"type": "object"}, "error": nullable(ID)}),
    "plan": tagged("plan", {"suite": ref("suite"), "workload": ref("workload"),
        "adapters": arr(ref("adapter-descriptor"), 32), "tokenizer": ref("tokenizer"),
        "renderer": {"const": "cgb.source-spans.v1"}, "seed": N,
        "timeout_ms": POS, "replicates": {**POS, "maximum": 100},
        "sampling_unit": {"enum": ["task-cluster", "process-cohort"]},
        "cells": arr(ref("cell"), 50000), "harness_sha256": HASH}),
    "attempt": tagged("attempt", {"cell": ref("cell"), "status": STATUS,
        "request_path": nullable(S), "response_path": nullable(S),
        "journal_path": S, "stderr_path": S, "started_ns": {"type": "string", "pattern": "^[0-9]+$"},
        "finished_ns": {"type": "string", "pattern": "^[0-9]+$"},
        "query_ms": nullable({"type": "number", "minimum": 0}),
        "startup_ms": nullable({"type": "number", "minimum": 0}),
        "returncode": nullable({"type": "integer"}), "pid": nullable(POS), "cleanup": {"type": "boolean"},
        "error": nullable(ID), "phases": arr(obj({"operation": ID, "milliseconds": {"type": "number", "minimum": 0}, "status": STATUS}), 2048)}),
    "manifest": tagged("manifest", {"kind": {"enum": ["run", "report", "dataset", "outcome", "platform-comparison", "portable-soak"]},
        "disposition": {"enum": ["complete", "partial"]}, "artifacts": arr(ref("artifact"), 50000)}),
    "result": tagged("result", {"plan_sha256": HASH, "labels_sha256": HASH,
        "evaluator_sha256": HASH, "suite": ref("suite"), "tokenizer": ref("tokenizer"),
        "renderer": S, "complete": {"type": "boolean"}, "evidence_class": S,
        "comparative_qualified": {"const": False}, "treatments": arr(ref("adapter-descriptor"), 32), "adapters": {"type": "object"},
        "comparisons": arr({"type": "object"}), "observations": arr({"type": "object"})}),
}

REQUEST_PAYLOADS = {
    "describe": obj({}), "reset": obj({"namespace": ID}), "close": obj({"namespace": ID}),
    "ingest": obj({"documents": arr(ref("document")), "edges": arr(ref("edge"))}),
    "query": obj({"namespace": ID, "query": S, "scope": ref("scope"), "as_of": nullable(S),
                  "required_ids": arr(ID), "budget": POS, "timeout_ms": POS}),
}
for operation in ("update", "delete", "set_scope", "handoff"):
    REQUEST_PAYLOADS[operation] = obj({"documents": arr(ref("document")), "ids": arr(ID), "scope": nullable(ref("scope"))})
DEFS["adapter-request"]["allOf"] = [
    {"if": {"properties": {"operation": {"const": operation}}},
     "then": {"properties": {"payload": payload}}}
    for operation, payload in REQUEST_PAYLOADS.items()
]
BLOCK = obj({"text": S, "citations": {**arr(ref("span"), 4096), "minItems": 1}})
QUERY_RESULT = obj({"blocks": arr(BLOCK, 4096), "observed_revision": ID,
                    "usage": nullable({"type": "object"})})
ACK = obj({"revision": ID, "ready": {"type": "boolean"}})
DESCRIPTION = obj({"engine": S, "version": S, "capabilities": arr(ID, 64),
                   "boundary": S, "preprocessing": S})
DEFS["adapter-response"]["allOf"] = [{
    "if": {"properties": {"status": {"const": "ok"}}},
    "then": {"properties": {"payload": {"oneOf": [QUERY_RESULT, ACK, DESCRIPTION]}, "error": NULL}},
    "else": {"properties": {"payload": obj({}), "error": ID}},
}]

DEFS["test-pack"] = tagged("test-pack", {
    "id": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,47}/[a-z][a-z0-9-]{0,63}$"},
    "version": {"type": "string", "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+(?:-[a-z0-9.-]+)?$"},
    "title": S, "description": S, "license": S,
    "authors": {**arr(obj({"name": S, "affiliation": S}), 32), "minItems": 1},
    "evidence_class": {"const": "public-development"}, "component": {"enum": ["security", "privacy", "experimental"]},
    "driver": {"const": "paired-agent.v1"}, "grader": {"const": "safe-useful-family-macro.v1"},
    "required_capabilities": arr({"enum": ["update", "delete", "set_scope", "handoff"]}, 4),
    "families": {**arr(ID, 32), "minItems": 1, "uniqueItems": True},
    "limits": obj({k: {**POS, "maximum": maximum} for k, maximum in {
        "context_tokens": 16384, "model_calls_per_arm": 8, "case_seconds": 300, "trace_bytes": 8*1024**2}.items()}),
    "cases": {**arr(obj({k: {"type": "string", "maxLength": 16384} for k in
        ("id", "family", "domain", "title", "scenario", "question", "attack", "carrier")}), 256), "minItems": 1},
})

REGISTRY = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": DEFS}
