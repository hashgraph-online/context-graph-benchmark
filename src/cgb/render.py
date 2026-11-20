"""Neutral source-span validation and cgb.source-spans.v1 rendering."""

from .contracts import timestamp, validate
from .jsonio import encoded, require

RENDERER = "cgb.source-spans.v1"


def render(blocks):
    # One canonical JSON block per line, exact supplied order, framing included.
    return b"".join(encoded(block) for block in blocks).decode("utf-8")


def initial_state(public, task):
    documents = list(public["documents"])
    active_scope = {"project": None, "allowed_ids": None}
    deleted = set()
    for event in task["events"]:
        operation = event["operation"]
        if operation == "update":
            for document in event["documents"]:
                documents = [d for d in documents if (d["id"], d["version"]) != (document["id"], document["version"])]
                documents.append(document)
                deleted.discard(document["id"])
        elif operation == "delete":
            deleted.update(event["ids"])
        elif operation in ("set_scope", "handoff"):
            active_scope = event["scope"] or active_scope
    return documents, deleted, active_scope


def visible(documents, deleted, scope, active_scope, as_of):
    # Last acknowledged version wins without dates; validity dates govern dated sources.
    selected = {}
    moment = timestamp(as_of)
    for document in documents:
        if document["id"] in deleted:
            continue
        if any(s["project"] is not None and document["project"] != s["project"] for s in (scope, active_scope)):
            continue
        if any(s["allowed_ids"] is not None and document["id"] not in s["allowed_ids"] for s in (scope, active_scope)):
            continue
        start, end = timestamp(document["valid_from"]), timestamp(document["valid_to"])
        if moment is not None and ((start is not None and start > moment) or (end is not None and end <= moment)):
            continue
        prior = selected.get(document["id"])
        if prior is None or start is None or prior["valid_from"] is None or start >= timestamp(prior["valid_from"]):
            selected[document["id"]] = document
    return selected


def verify_context(blocks, public, task, budget, counter):
    require(isinstance(blocks, list) and len(blocks) <= 4096, "invalid evidence blocks")
    documents, deleted, active_scope = initial_state(public, task)
    admitted = visible(documents, deleted, task["scope"], active_scope, task["as_of"])
    delivered, violations = [], []
    for block in blocks:
        require(type(block) is dict and set(block) == {"text", "citations"}, "invalid evidence block")
        require(isinstance(block["text"], str) and block["text"] and len(block["text"].encode()) <= 1048576, "invalid block text")
        require(isinstance(block["citations"], list) and 0 < len(block["citations"]) <= 4096, "missing citations")
        seen = set()
        for citation in block["citations"]:
            validate("span", citation)
            key = (citation["document_id"], citation["version"], citation["start"], citation["end"])
            require(key not in seen, "duplicate citation")
            seen.add(key)
            document = admitted.get(citation["document_id"])
            if document is None:
                violations.append("unauthorized-or-deleted-source")
            elif document["version"] != citation["version"]:
                violations.append("stale-source")
            elif not citation["start"] < citation["end"] <= len(document["text"]) or document["text"][citation["start"]:citation["end"]] != block["text"]:
                violations.append("fabricated-source-span")
            else:
                delivered.append(citation)
    text = render(blocks)
    tokens = counter.count(text)
    if tokens > budget:
        violations.append("budget-overrun")
    return {"rendered": text, "tokens": tokens, "spans": delivered, "violations": sorted(set(violations))}
