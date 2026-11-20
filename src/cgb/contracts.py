"""Closed schema validation plus source/workload cross-record checks."""

import re
from datetime import datetime

from jsonschema import Draft202012Validator, validators

from .jsonio import Invalid, encoded, require
from .schema_defs import REGISTRY

StrictValidator = validators.extend(Draft202012Validator, type_checker=
    Draft202012Validator.TYPE_CHECKER.redefine("integer", lambda checker, value: type(value) is int))


def validate(kind, value):
    require(kind in REGISTRY["$defs"], "unknown schema kind")
    schema = {**REGISTRY, "$ref": "#/$defs/" + kind}
    try:
        StrictValidator(schema).validate(value)
        encoded(value)
    except (ValueError, TypeError) as exc:
        raise Invalid("invalid " + kind) from exc
    except Exception as exc:
        from jsonschema import ValidationError
        if isinstance(exc, ValidationError):
            raise Invalid("invalid " + kind + " at " + "/".join(str(p) for p in exc.path)) from exc
        raise
    return value


def unique(values, label):
    require(len(values) == len(set(values)), "duplicate " + label)


def timestamp(value):
    if value is None:
        return None
    require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)), "UTC timestamp required")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Invalid("invalid timestamp") from exc


def check_documents(documents):
    unique([(d["id"], d["version"]) for d in documents], "document/version")
    for document in documents:
        start, end = timestamp(document["valid_from"]), timestamp(document["valid_to"])
        require(start is None or end is None or start < end, "invalid validity interval")
        require(bool(document["text"]), "empty evidence document")


def workload(value):
    validate("workload", value)
    check_documents(value["documents"])
    unique([t["id"] for t in value["tasks"]], "task")
    require(bool(value["tasks"]), "empty task inventory")
    ids = {d["id"] for d in value["documents"]}
    versions = {(d["id"], d["version"]): d for d in value["documents"]}
    for edge in value["edges"]:
        require(edge["from"] in ids and edge["to"] in ids, "edge endpoint unavailable")
    for task in value["tasks"]:
        timestamp(task["as_of"])
        unique(task["required_ids"], "required ID")
        if task["scope"]["allowed_ids"] is not None:
            unique(task["scope"]["allowed_ids"], "scope ID")
        for event in task["events"]:
            check_documents(event["documents"])
            unique(event["ids"], "event ID")
            operation = event["operation"]
            require((bool(event["documents"]) and not event["ids"] and event["scope"] is None) if operation == "update" else
                    (bool(event["ids"]) and not event["documents"] and event["scope"] is None) if operation == "delete" else
                    (event["scope"] is not None and not event["documents"] and not event["ids"]), "event payload does not match operation")
            if event["scope"] is not None and event["scope"]["allowed_ids"] is not None:
                unique(event["scope"]["allowed_ids"], "event scope ID")
            for document in event["documents"]:
                key = (document["id"], document["version"])
                require(key not in versions or versions[key] == document, "changed immutable source version")
                versions[key] = document
    return value


def labels(value, public):
    from .jsonio import digest
    validate("labels", value)
    require(value["workload_sha256"] == digest(encoded(public)), "labels/workload identity mismatch")
    unique([r["task"] for r in value["tasks"]], "label task")
    require({r["task"] for r in value["tasks"]} == {t["id"] for t in public["tasks"]}, "incomplete labels")
    documents = {(d["id"], d["version"]): d for d in public["documents"]}
    for task in public["tasks"]:
        for event in task["events"]:
            for document in event["documents"]:
                key = (document["id"], document["version"])
                require(key not in documents or documents[key] == document, "changed immutable source version")
                documents[key] = document
    clusters = {}
    domains = {t["id"]: t["domain"] for t in public["tasks"]}
    for row in value["tasks"]:
        unique([u["id"] for u in row["units"]], "evidence unit")
        require(bool(row["units"]) == (row["expectation"] == "answerable"), "answerability/unit mismatch")
        previous = clusters.setdefault(row["cluster"], domains[row["task"]])
        require(previous == domains[row["task"]], "cluster crosses domains")
        for unit in row["units"]:
            for alternative in unit["alternatives"]:
                for span in alternative:
                    key = (span["document_id"], span["version"])
                    require(key in documents, "label source unavailable")
                    require(span["start"] < span["end"] <= len(documents[key]["text"]), "label span outside source")
    return value
