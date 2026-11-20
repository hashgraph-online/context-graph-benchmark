# Portable agent soak: 1 hour and 24 hours

Both schedules are part of the platform, available in Run Studio and the CLI. Each selected engine receives the **full duration**, sequentially on the same host. Selecting three engines for 24 hours means roughly 72 hours plus setup and cleanup. These are public development simulations, not production security certifications.

```bash
# Validate installation and cleanup first; never endurance-qualified.
cgb platform soak --root . --engines cigar networkx lean-ctx --pilot-seconds 15

# Registered one-hour schedule, per engine.
cgb platform soak --root . --engines cigar networkx lean-ctx --hours 1

# Registered day-long schedule, per engine.
cgb platform soak --root . --engines cigar networkx lean-ctx --hours 24

# Add a fixed local model; the same reader is used for all selected engines.
cgb platform soak --root . --engines cigar networkx lean-ctx \
  --hours 1 --reader .cgb/readers/my-model.json --baseline networkx

cgb platform soak-replay lab/portable-soaks/RUN_ID
```

## Fixed orchestration

| Parameter | 1 hour | 24 hours |
| --- | --- | --- |
| Measured duration per engine | 3,600 seconds | 86,400 seconds |
| Persistent adapter/agent lanes | 12 | 12 |
| Teams | 4 × planner/operator/reviewer | Same |
| Query cadence | Every 5 seconds | Same |
| Registered context-query denominator | 8,640 | 207,360 |
| Source fact mutation | Every 60 seconds | Same |
| Scope/handoff counterfactual | Every 120 seconds | Same |
| Scheduled adapter restart | Every 900 seconds | Same |
| Model handoff wave, if reader configured | Every 180 seconds | Same |
| Model calls per wave | 3, sequential roles | Same |
| Common rendered context budget | 1,024 tokens | Same |
| Seed | 41 | 41 |

The fictional release-management scenario requires current owners, capacity arithmetic, artifact and rollback references, dependency readiness, check counts and policy decisions. Withdrawn artifacts require abstention where facts are unavailable. Source facts change; the evaluator generates typed targets separately. All inputs are synthetic and licensed with this project. Twelve lanes and repeated cycles share one generated scenario and are not independent task samples.

The adapter processes persist between queries and are restarted on schedule. An individual engine's adapter may rebuild its graph or native worker inside every query; that work remains timed and its profile must be disclosed. Portable restarts concern the generic adapter boundary. They are not a claim of native broker crash recovery.

## KPIs and accounting

Context KPIs include registered/observed/correct cycles, unobserved slots, required-source completeness, source version/scope/budget violations, query p50/p95/p99, control results, restart cleanup, duration and sampled adapter RSS. Resource sampling covers owned adapter PIDs and may miss transient native subprocess peaks; it is labeled accordingly. The engine's claimed pass flags are not used as correctness labels.

With a fixed reader, report complete typed-answer accuracy, correct abstention, fresh three-role workflow completion, all observed input/output model tokens, tokens per correct answer, context tokens per correct answer and tokens saved per correct answer relative to the selected baseline. Safe native commit authorization is not inferred from portable workflow completion.

`tokens per correct answer = sum(all observed reader input + output tokens) / complete correct answers`. Incorrect and failed dispatched attempts retain their cost. If a dispatched attempt has unknown usage, token cost is unavailable. If no answers are correct, the denominator is undefined. No reader means answer metrics are unavailable, not zero.

`tokens saved per correct answer = baseline tokens per correct answer − candidate tokens per correct answer`. It uses observed usage and independent denominators under the same reader/workload. Report both accuracies: a cheaper but less accurate system has not established equivalent-quality savings. Negative values remain negative. Reader tokens are not a full pipeline bill for arbitrary adapters that perform undisclosed inference.

Model calls currently run synchronously between query rounds. Slow models may cause missed query slots, which stay in the registered denominator. This treatment is explicit and versioned; it must not be compared to an asynchronous model schedule under the same profile identity.

## Evidence and controls

Plans, evaluator identity, engine artifacts, reader identity, raw adapter protocol streams, source phases, captured context, complete reader requests/responses and result reductions are retained under `lab/portable-soaks/`. Protocol and observation streams are gzip-compressed to keep a full-day run practical. Replay reads bounded data, verifies source mappings and request bindings, and never invokes an adapter or model.

A reader-enabled study registers `portable-reader-deadlines.v2`. Routine answers have 30 seconds and paired assurance answers have 120 seconds. The local MLX transport can wait up to 120 additional seconds for a correlated native token receipt from the same request. A late reply records a failed answer and its actual usage. The model worker is reaped before the next registered request. A missing receipt leaves usage unknown. Time spent waiting counts against the registered schedule. Full study replay verifies the frozen deadlines and raw observations.

The live dashboard shows current engine, elapsed duration, rolling query timing and lifetime cycle counts. Final metrics use the full stream. Stop from the live dashboard, send Ctrl-C, or create a `STOP` file in the owned run directory. Stopped runs retain partial evidence and never qualify as completed endurance runs. A pilot is always labeled diagnostic even when its short requested duration completes.

## Assurance and full studies

The assurance-enabled profile schedules paired cases every 300 seconds. It retains original context, reader attempts, case outcomes and native token usage for replay. The full macro study binds this endurance track to the registered evidence and graph suites with the same engine artifacts, evaluator, reader and tokenizer. The one-hour and 24-hour policies have separate identities. A pilot records setup and behavior over its shorter schedule and cannot receive a duration-qualified score. [Test packs](test-pack-spec.md) · [Scoring specification](overall-score.md)
