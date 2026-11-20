"""Real baseline/SQLite adapters; the CIGAR backend is imported only on request."""

import argparse
from collections import Counter as Frequencies
import math
import os
import re
import sqlite3
import sys

from .contracts import validate, workload
from .jsonio import Invalid, MAX_LINE, decode, encoded, require
from .render import render, visible
from .tokenization import Counter

CAPABILITIES = ["update", "delete", "set_scope", "handoff", "model-free-retrieval.v1"]


class Engine:
    def __init__(self, name, counter, sdk_root=None, worker=None, library_manifest=None):
        self.name, self.counter = name, counter
        self.sdk_root, self.worker = sdk_root, worker
        self.library_manifest = library_manifest
        self.reset("initial")

    def reset(self, namespace):
        self.namespace, self.documents, self.edges = namespace, [], []
        self.deleted, self.active, self.revision = set(), {"project": None, "allowed_ids": None}, 0

    def ranking(self, documents, query):
        ordered = sorted(documents)
        if self.name == "empty":
            return []
        if self.name == "recent-window":
            return sorted(ordered, key=lambda key: (documents[key]["valid_from"] or "", key), reverse=True)
        terms = sorted(set(re.findall(r"[A-Za-z0-9_]+", query.lower())))[:64]
        if self.name == "sqlite-fts5":
            with sqlite3.connect(":memory:") as connection:
                connection.execute("CREATE VIRTUAL TABLE corpus USING fts5(id UNINDEXED, body)")
                connection.executemany("INSERT INTO corpus VALUES (?, ?)", [(key, documents[key]["text"]) for key in ordered])
                expression = " OR ".join('"' + term + '"' for term in terms)
                if not expression:
                    return []
                return [row[0] for row in connection.execute("SELECT id FROM corpus WHERE corpus MATCH ? ORDER BY bm25(corpus), id", (expression,))]
        counts = {key: Frequencies(re.findall(r"[A-Za-z0-9_]+", documents[key]["text"].lower())) for key in ordered}
        mean_length = sum(sum(value.values()) for value in counts.values()) / max(1, len(counts))
        document_frequency = {term: sum(term in value for value in counts.values()) for term in terms}
        scores = {}
        for key in ordered:
            score = 0.0
            length = sum(counts[key].values())
            for term in terms:
                frequency = counts[key][term]
                df = document_frequency[term]
                idf = math.log(1 + (len(counts) - df + .5) / (df + .5))
                if frequency:
                    score += idf * frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * length / max(mean_length, 1)))
            if score > 0:
                scores[key] = round(score, 9)
        return sorted(scores, key=lambda key: (-scores[key], key))

    @staticmethod
    def block(document):
        return {"text": document["text"], "citations": [{"document_id": document["id"], "version": document["version"], "start": 0, "end": len(document["text"])}]}

    def query(self, payload):
        documents = visible(self.documents, self.deleted, payload["scope"], self.active, payload["as_of"])
        if self.name == "cigar":
            return self.cigar_query(documents, payload)
        selected = []
        backend = None
        if self.name == "networkx":
            from .graph_backends import GraphBackend
            backend = GraphBackend(self.name, documents, self.edges)

        def closure(root):
            if backend is not None:
                return backend.closure(root)
            pending, reached = [root], set()
            while pending:
                key = pending.pop()
                if key in reached:
                    continue
                if key not in documents:
                    return None
                reached.add(key)
                pending.extend(edge["to"] for edge in self.edges if edge["from"] == key and edge["kind"] in ("requires", "contradicts"))
            return sorted(reached)

        for key in payload["required_ids"]:
            items = closure(key)
            if items is None or self.name == "empty":
                return "refused", {}, "required-unavailable"
            selected.extend(item for item in items if item not in selected)
        blocks = [self.block(documents[key]) for key in selected]
        if self.counter.count(render(blocks)) > payload["budget"]:
            return "refused", {}, "mandatory-budget"
        for key in self.ranking(documents, payload["query"]):
            if key in selected:
                continue
            items = closure(key)
            if items is None:
                continue
            trial = selected + [item for item in items if item not in selected]
            trial_blocks = [self.block(documents[item]) for item in trial]
            if self.counter.count(render(trial_blocks)) <= payload["budget"]:
                selected, blocks = trial, trial_blocks
        return "ok", {"blocks": blocks, "observed_revision": "r" + str(self.revision), "usage": {"model_input_tokens": 0, "model_output_tokens": 0}}, None

    def cigar_query(self, documents, payload):
        # Trusted-host authorized view composition is adapter work, not broker authorization.
        require(self.sdk_root is not None and self.worker is not None, "CIGAR paths required")
        sys.path.insert(0, self.sdk_root)
        from cigar_sdk.context import LocalContextError, LocalContextGraph
        with LocalContextGraph("cgb-adapter", worker_path=self.worker) as graph:
            for document in documents.values():
                graph.upsert({"id": document["id"], "source": document["source"], "text": document["text"], "start_line": 1})
            missing = set()
            for edge in self.edges:
                if edge["from"] in documents and edge["kind"] in ("requires", "contradicts") and edge["to"] not in documents:
                    missing.add(edge["to"])
            for identity in sorted(missing):
                graph.upsert({"id": identity, "source": "cgb-unavailable", "text": "Unavailable source placeholder."})
            for edge in self.edges:
                if edge["from"] in documents and (edge["to"] in documents or edge["to"] in missing) and edge["kind"] != "supersedes":
                    graph.link(edge["from"], edge["to"], edge["kind"])
            for identity in sorted(missing):
                graph.remove(identity)
            request = {"query": payload["query"], "allowed": sorted(documents), "required": payload["required_ids"],
                       "max_tokens": payload["budget"], "max_blocks": 4096, "max_candidates": 4096}
            try:
                result = graph.compile(request)
            except LocalContextError as exc:
                if exc.code in ("RequiredUnavailable", "BudgetUnsatisfiable"):
                    return "refused", {}, "native-" + exc.code.lower()
                return "error", {}, "native-" + exc.code.lower()
            blocks = []
            for block in result["snapshot"]["blocks"]:
                citations = []
                for citation in block["citations"]:
                    document = documents[citation["node_id"]]
                    start = document["text"].find(block["text"])
                    require(start >= 0, "native text cannot be mapped")
                    citations.append({"document_id": document["id"], "version": document["version"], "start": start, "end": start + len(block["text"])})
                blocks.append({"text": block["text"], "citations": citations})
            return "ok", {"blocks": blocks, "observed_revision": "r" + str(self.revision), "usage": {"native": result, "model_input_tokens": 0, "model_output_tokens": 0}}, None

    def handle(self, operation, payload):
        if operation == "describe":
            version = sqlite3.sqlite_version if self.name == "sqlite-fts5" else "1"
            preprocessing = "trusted scope/time filtering and Python relationship closure; ranked full-source packing"
            if self.name == "networkx":
                import importlib.metadata
                try:
                    __import__(self.name)
                    version = importlib.metadata.version(self.name)
                    if self.library_manifest is not None:
                        from .graph_backends import check_distribution
                        check_distribution(self.library_manifest, self.name)
                    preprocessing = "trusted scope/time filtering, BM25 ranking and full-source packing; "+self.name+" native reachability; graph rebuilt per query"
                except ImportError:
                    return "unsupported", {}, "library-not-installed"
            if self.name == "cigar":
                sys.path.insert(0, self.sdk_root)
                from cigar_sdk.local_runtime import LOCAL_CONTEXT_CORE_VERSION
                version = LOCAL_CONTEXT_CORE_VERSION
                preprocessing = "trusted scope/time filtering; authorized native CIGAR view rebuilt inside each query, including worker startup"
            elif self.name == "sqlite-fts5":
                preprocessing = "trusted scope/time filtering and Python closure; native FTS5 index rebuilt and ranked per query"
            return "ok", {"engine": self.name, "version": version, "capabilities": CAPABILITIES,
                           "boundary": "trusted-host-adapter", "preprocessing": preprocessing}, None
        if operation == "reset":
            self.reset(payload["namespace"])
        elif operation == "ingest":
            workload({"schema": "cgb.workload.v1", "id": "adapter-input", "documents": payload["documents"], "edges": payload["edges"],
                      "tasks": [{"id": "validation", "domain": "validation", "family": None, "query": "validate", "scope": {"project": None, "allowed_ids": None}, "as_of": None, "required_ids": [], "events": []}]})
            self.documents, self.edges = payload["documents"], payload["edges"]
            self.revision += 1
        elif operation == "update":
            for document in payload["documents"]:
                self.documents = [d for d in self.documents if (d["id"], d["version"]) != (document["id"], document["version"])]
                self.documents.append(document)
                self.deleted.discard(document["id"])
            self.revision += 1
        elif operation == "delete":
            self.deleted.update(payload["ids"])
            self.revision += 1
        elif operation in ("set_scope", "handoff"):
            self.active = payload["scope"]
            self.revision += 1
        elif operation == "query":
            require(payload["namespace"] == self.namespace, "unknown namespace")
            return self.query(payload)
        elif operation == "close" and self.library_manifest is not None:
            from .graph_backends import check_distribution
            check_distribution(self.library_manifest, self.name)
        elif operation != "close":
            return "unsupported", {}, "unsupported-operation"
        return "ok", {"revision": "r" + str(self.revision), "ready": True}, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["empty", "recent-window", "bm25", "sqlite-fts5", "cigar", "networkx"], required=True)
    parser.add_argument("--sdk-root")
    parser.add_argument("--worker")
    parser.add_argument("--library-manifest")
    args = parser.parse_args()
    engine = Engine(args.engine, Counter(os.environ["CGB_TOKENIZER"]), args.sdk_root, args.worker, args.library_manifest)
    while payload := sys.stdin.buffer.readline(MAX_LINE + 1):
        require(payload.endswith(b"\n") and len(payload) <= MAX_LINE, "invalid request framing")
        request = validate("adapter-request", decode(payload, MAX_LINE))
        try:
            status, result, error = engine.handle(request["operation"], request["payload"])
        except (Invalid, ValueError, KeyError, RuntimeError) as exc:
            print(type(exc).__name__, file=sys.stderr, flush=True)
            status, result, error = "error", {}, "engine-error"
        response = {"schema": "cgb.adapter-response.v1", "id": request["id"], "status": status, "payload": result, "error": error}
        sys.stdout.buffer.write(encoded(response))
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
