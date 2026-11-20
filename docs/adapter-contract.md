# Engine adapter contract

Implemented transport: `cgb.adapter-request.v1` / `cgb.adapter-response.v1`, UTF-8 JSONL over a subprocess's stdin/stdout. Executable definitions are in [schema_defs.py](../src/cgb/schema_defs.py) and [exported schemas](../schemas/contracts-v1.schema.json).

## Wire behavior

One request receives one terminal response with the same ID. Stdout is protocol only; stderr is diagnostics. Use argument arrays, not shell commands. Requests and response lines are bounded at 8 MiB, total stdout at 64 MiB and stderr at 8 MiB per process. Deadlines apply to input writes and response reads. Invalid/truncated/duplicate-key/nonfinite JSON and wrong response IDs are failures; original bytes remain in the run.

```json
{"schema":"cgb.adapter-request.v1","id":"request-5","operation":"query","payload":{"namespace":"cell-000001","query":"What is the retry limit?","scope":{"project":"alpha","allowed_ids":null},"as_of":null,"required_ids":[],"budget":2048,"timeout_ms":5000}}
```

```json
{"schema":"cgb.adapter-response.v1","id":"request-5","status":"ok","payload":{"blocks":[{"text":"Retry once.","citations":[{"document_id":"policy-2","version":"r2","start":0,"end":11}]}],"observed_revision":"r2","usage":null},"error":null}
```

Offsets are half-open Unicode code points into unchanged original text. Translate native byte/UTF-16 offsets. The common budget counts canonical rendered blocks including citation framing; response JSON overhead and diagnostic usage are outside that delivered-context rendering.

## Operations and exact successful payloads

| Operation | Request payload | Successful response payload |
| --- | --- | --- |
| `describe` | `{}` | `engine`, `version`, `capabilities` (string list), `boundary`, `preprocessing` |
| `reset` | `namespace` | `revision` and `ready` |
| `ingest` | `documents` and `edges` | `revision` and `ready` |
| `update`, `delete`, `set_scope`, `handoff` | Always `documents`, `ids`, `scope`; only the operation's relevant field is populated | `revision` and `ready` |
| `query` | Complete example above | `blocks`, `observed_revision`, `usage` |
| `close` | `namespace` | `revision` and `ready`, followed by clean process exit |

All fields shown are required and unknown fields are rejected. `usage` is an optional-content object or null, with diagnostic engine data never trusted as quality. `scope` always has `project` and `allowed_ids`; null means no extra restriction. Application scope and the current active scope intersect.

An update has nonempty `documents`, empty `ids`, null `scope`; a delete has nonempty `ids`, empty `documents`, null `scope`; a scope/handoff event has empty arrays and non-null `scope`. Event acknowledgements must have `ready:true`. The current controller rejects asynchronous readiness instead of assuming search visibility. Polling, checkpoint/restore, HTTP, and opaque-summary results are future extensions.

Non-success status is one of `abstained`, `refused`, `unsupported`, `error`, with empty payload and a non-null normalized error ID. Success has `error:null`. The controller records `timeout` and ordinary process errors separately. No retry replaces a primary attempt.

## State and evidence

Each cell gets a fresh process and namespace: describe, reset, ingest, ordered events, one query, close. There is no cross-cell persistent state. Graph fixtures exercise scope replacement within one episode; they do not establish restored or durable actor state.

Evidence blocks contain exactly `text` and nonempty `citations`. Each citation must match the same contiguous block text in an admitted original source/version. The scorer neither fetches omitted text from an ID nor truncates an oversized answer. It preserves ordering and checks all framing tokens. Repeated evidence cannot earn a unit twice.

Edges point between logical source IDs. `A requires B` and `A contradicts B` make B part of A's packing closure in the bundled adapters. The engine's own edge handling remains part of its declared treatment. Public application-required IDs are allowed; hidden gold relevance must not be inserted there.

## Fairness and scope

Bind the engine, adapter, configuration, command and declared artifact hashes. Record actual runtime descriptions alongside every attempt. The bundled SQLite adapter rebuilds an FTS5 index per query. The CIGAR adapter rebuilds a scope/time-filtered authorized native view; its query timing includes worker startup and that work. These are observable adapter configurations, not assertions about native incremental indexing or authenticated broker enforcement.

The controller omits labels from protocol messages, launches with a small environment, and records `label_isolation:not-certified`. It does not deny filesystem access under the host user. Reviewed hidden-label/authorization studies require an independently checked sandbox or broker that is not yet implemented.

[Engine bindings](../README.md#run-a-scored-study) · [Public JSON schemas](../schemas/contracts-v1.schema.json)
