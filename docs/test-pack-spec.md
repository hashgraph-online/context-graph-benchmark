# Data-only test packs — executable v1

The SDK accepts bounded data-only manifests. Built-in packs contribute to the portable score through the assurance-enabled study.

## Layers

An **engine adapter** implements the generic JSONL engine protocol. A **test pack** supplies bounded JSON case data. An **execution driver** orchestrates the registered lifecycle and observations. An **offline grader** derives outcomes from raw traces and supervisor-owned labels. A **scoring profile** selects exact pack versions and weights. The dashboard renders sanitized metadata/results using local reviewed code.

These layers have separate identities. Installing an experimental pack cannot change a core score, download dependencies, import a contributor entry point, request network access, call a hosted model, or publish data. No manifest-supplied HTML, JavaScript, Python, shell, executable import path or remote schema is accepted.

## Manifest

The authoritative closed JSON Schema is `test-pack` in [the contract registry](../schemas/contracts-v1.schema.json), with additional semantic validation in [registry.py](../src/cgb/test_packs/registry.py). Inspect [the Security pack](../src/cgb/data/test-packs/security.v1.json) and [Privacy pack](../src/cgb/data/test-packs/privacy.v1.json) for complete working examples.

Required fields:

| Field | Contract |
| --- | --- |
| schema | `cgb.test-pack.v1` |
| id, version | Own `namespace/name`, immutable semantic version |
| title, description, license, authors | Bounded metadata and affiliation disclosure |
| evidence_class | `public-development`; a manifest cannot declare itself held-out |
| component | `security`, `privacy` or `experimental`; core eligibility is assigned by the trusted registry |
| driver | Reviewed installed ID, currently `paired-agent.v1` |
| grader | Reviewed installed ID, currently `safe-useful-family-macro.v1` |
| required_capabilities | Explicit `update`, `delete`, `set_scope`, `handoff` operations |
| families | Unique registered family IDs; every family needs cases |
| limits | Context tokens, calls per arm, dispatch deadline per arm, bounded trace bytes |
| cases | Unique IDs, family, domain, title, scenario, question, attack, carrier |

The reviewed scenarios are source-injection, tool-injection, memory-poisoning, action-redirection, scope-separation, revocation, never-provided and sink-containment. The carrier is source, tool-result, handoff or memory. Arbitrary case text is data. Only `{{attack_marker}}` may be substituted in attack text; private targets cannot be templated into it. Cases cannot redefine the grader, source permissions, hidden targets or global denominator through an opaque predicate string.

At most 128 engine/case pairs can be registered per standalone run. Individual manifests, protocol frames, traces, dispatch deadlines and model calls have fixed limits. The per-arm deadline is checked before stages and reader dispatch; one in-flight query/model call retains its separate bounded completion timeout. Community packs cannot shadow `hol/` or overwrite an already registered version. Discovery is read-only; registration is a create-new local operation. The frozen plan includes full manifests/digests, driver/evaluator source identity, engine artifacts, tokenizer and reader settings, not just human-readable version labels.

## Add a test without editing the harness

```bash
cgb test-pack scaffold --id example/handoff-boundary --output test-packs/handoff
# Edit test-packs/handoff/pack.json; retain the closed schema.
cgb test-pack validate test-packs/handoff/pack.json
cgb test-pack conformance test-packs/handoff/pack.json
cgb test-pack register --root . test-packs/handoff/pack.json
cgb test-pack list --root .
cgb test-pack run --root . --engines networkx \
  --reader .cgb/readers/fixed.json --packs example/handoff-boundary@0.1.0
cgb test-pack replay lab/assurance/RUN_ID
```

1. Describe the user problem and threat assumptions. Use synthetic or licensed data and disclose provenance/affiliation.
2. Pick an existing scenario. Edit the task and attack, preserving useful positive controls. Keep hidden targets outside execution inputs.
3. Validate and run deterministic conformance. Conformance checks contracts and mock permissions; it does not measure engine/model effectiveness or prove safety.
4. Execute an experimental cohort with an explicit fixed model. Retain first attempts, errors, unknown usage and raw evidence. Replay it offline.
5. Inspect the new pack in the dashboard catalogue and Run Studio. It appears automatically without editing UI choices or CLI dispatch.
6. Submit a [test-pack proposal](../.github/ISSUE_TEMPLATE/test-pack.yml), the JSON manifest, license/provenance, conformance output and reviewed sanitized evidence. Do not attach private raw bundles automatically.
7. Propose core promotion separately through [governance](../GOVERNANCE.md). It requires an RFC, unaffiliated review, new locked profile and prospective cohort. Merge alone cannot grant independent qualification.

Adding/removing a case, changing a payload, source policy, decoder or metric creates a new immutable pack/driver/profile version. Repeated attack variants and soak cycles are nested under base cases before family averaging. Installing more easy tests cannot dilute a hard core family.

## Add an execution/test adapter

A genuinely new interaction needs reviewed code, rather than executable manifest fields. Submit a source PR implementing a bounded executor and a data-only grader with a new ID. The driver interface takes the owned run directory, registered descriptor/case, supervisor oracle, fixed reader/tokenizer, supervisor model callback and confinement mode, and returns paths to bounded raw observations. See `execute_pair` and `grade_pair` in [runtime.py](../src/cgb/test_packs/runtime.py) and [grading.py](../src/cgb/test_packs/grading.py).

Keep the executor unaware of correctness targets except the supervisor's declared state construction; do not forward oracle fields into engine/model inputs. Separate writable engine work from protected journals. Record every ordered operation, context span, model request/response and observed usage, mock action/permission decision, handoff/restart, timeout and cleanup. A new native authorization driver must not inherit reference-gateway credit silently.

The trusted allowlists, schema and dispatch must be extended in the same reviewed source change. There is deliberately no third-party Python entry-point auto-discovery. Add conformance fixtures for leaking/refuse-all/fabricated/stale/unsupported/missing-observer behavior, process cleanup, malformed data, bounded cost and data-only replay. Prove that discovery imports no new contributor code and that scoring/replay starts no submitted engine/model/network process. Add separately versioned short and endurance contracts when relevant. See [adoption requirements](../CONTRIBUTING.md).

## Evidence and dashboard

The grader reconstructs case state and model messages, verifies raw protocol bytes and ordered setup/query operations, checks actual source spans, independently reduces mock effects, then calculates safe useful completion and leakage. Producer success booleans are not accepted as labels. Missing responses stay unknown. Unknown dispatched usage is unavailable; zero correct answers is undefined.

The supervisor's `private/` stores canaries and raw authorized responses. Public `result.json`/`state.json` contain derived redacted case evidence. The dashboard denies private/work artifacts, escapes text and renders no contributor code. Redaction occurs after raw grading and cannot earn a privacy pass. Offline snapshots include only collected public summaries and disable run/network controls.

Core full studies require the exact query and assurance-enabled endurance cohort. Community/partial packs retain their own component identity, with no weight renormalization or automatic macro score. [Scoring specification](overall-score.md)
