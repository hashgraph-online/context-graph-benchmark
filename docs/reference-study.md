# Reference scorecard

Study `20261120-release-three-engines-1h-01` covers 414 query cells and one hour of agent workflows per system. All three systems used the same host, workloads and reader. Each has nine measured components and passed the mandatory checks. The overall scoring policy was selected with the component measurements available.

| Measured system | Overall / 100 | Coverage | Model tokens per safe correct answer |
| --- | ---: | ---: | ---: |
| CIGAR Python SDK 0.14.2 | **93.51** | 100% | 638.95 |
| NetworkX 3.7 | **90.14** | 100% | 1,094.07 |
| LeanCTX 3.12.4 | **80.64** | 100% | 626.70 |

## Component scores

| Component | Weight | CIGAR Python SDK | NetworkX | LeanCTX |
| --- | ---: | ---: | ---: | ---: |
| Evidence retrieval | 10% | 100.00 | 100.00 | 0.00 |
| Graph behavior | 10% | 100.00 | 100.00 | 100.00 |
| Balanced answer quality | 15% | 100.00 | 98.33 | 100.00 |
| Safe workflow completion | 10% | 100.00 | 85.00 | 100.00 |
| Operational reliability | 10% | 88.40 | 85.63 | 88.26 |
| Token efficiency | 10% | 61.58 | 48.35 | 62.03 |
| Runtime efficiency | 5% | 70.28 | 69.92 | 12.23 |
| Security | 15% | 100.00 | 100.00 | 100.00 |
| Privacy | 15% | 100.00 | 100.00 | 100.00 |

For a worked example, six components contribute their full 75 points to the CIGAR 0.14.2 total. Operational reliability adds 8.8403 of 10 points, token efficiency adds 6.1577 of 10, and runtime efficiency adds 3.5138 of 5. Adding the contributions at full precision and rounding once gives **93.51/100**. [Scoring formulas](overall-score.md)

### Query measurements

Each engine received the same 32 evidence tasks and 14 graph episodes at 512, 2,048 and 4,096 tokens. The 28 answerable tasks contain 40 labeled evidence units per budget. Each system produced the counts below at all three budgets. The 2,048-token budget carries half the query weight and the other two carry a quarter each.

| Per-budget observation | CIGAR Python SDK | NetworkX | LeanCTX |
| --- | ---: | ---: | ---: |
| Evidence queries completed | 32 / 32 | 32 / 32 | 32 / 32 |
| Labeled evidence units retrieved | 40 / 40 | 40 / 40 | 0 / 40 |
| Graph episodes passed | 14 / 14 | 14 / 14 | 14 / 14 |

## Model tokens and answer credit

| System | Routine input / output tokens | Assurance input / output tokens | Engine model tokens, both tracks | All measured tokens | Fresh, safe correct answers | Late usage receipts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CIGAR Python SDK | 40,315 / 1,232 | 15,576 / 2,938 | 0 | 60,061 | 94 | 0 |
| NetworkX | 79,821 / 1,200 | 15,576 / 2,963 | 0 | 99,560 | 91 | 0 |
| LeanCTX | 39,139 / 1,232 | 15,576 / 2,963 | 0 | 58,910 | 94 | 0 |

Divide the all-track token count by the safe correct answers to reproduce the cost in the headline table. The numerator includes every dispatched first attempt, including incorrect answers. Correct abstentions can earn credit. A late native receipt records actual usage from that request and earns zero answer credit.

## Endurance and first attempts

| System | Measured seconds | Correct cycles / registered | On-time routine replies / registered | Fresh correct workflows / registered | Assurance pairs | Controls | Clean restarts | Soak query p95 | Sampled peak adapter memory |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CIGAR Python SDK | 3,600.01 | 6,636 / 8,640 | 60 / 60 | 20 / 20 | 12 / 12 | 29 / 29 | 3 / 3 | 28.56 ms | 952.09 MiB |
| NetworkX | 3,600.01 | 6,156 / 8,640 | 60 / 60 | 17 / 20 | 12 / 12 | 29 / 29 | 3 / 3 | 26.61 ms | 1,087.36 MiB |
| LeanCTX | 3,600.01 | 6,612 / 8,640 | 60 / 60 | 20 / 20 | 12 / 12 | 29 / 29 | 3 / 3 | 1,209.68 ms | 1,015.52 MiB |

The comparison returned 414 of 414 registered query cells, with 0 query failures and 0 recorded source violations. Each timed workload registered 8,640 cycles across twelve lanes. Synchronous reader calls consumed wall time. Missed cycle slots stayed in the denominator. The receipt also gives the observed cycles, individual answer strata, pack families and underlying memory and latency samples in aggregate.

## Reading these results

The systems ran sequentially in table order on one shared Apple M3 Ultra with 512 GiB of memory, macOS 26.6.1 arm64 and CPython 3.14.7. The MLX reader used Qwen3.8-27B-8bit, a 256-token answer cap, temperature 0, top-p 1, seed 41 and thinking disabled. Routine answers had 30 seconds and assurance answers had 120 seconds. Local MLX could wait another 120 seconds solely for a correlated native usage receipt.

CIGAR used the released macOS arm64 Python wheel and its bundled worker. NetworkX provided traversal inside the reference retrieval adapter. LeanCTX provided file-search ranking with reference graph closure and packing. Query timing includes adapter work. Periodic memory sampling can miss short-lived child processes. [CIGAR SDK release](https://github.com/hashgraph-online/hol-cigar/releases/tag/v0.14.2) · [LeanCTX adapter](engines/lean-ctx.md)

The query fixtures are public. Twelve assurance pairs per engine draw on eight base templates. The scores describe these implementations and this host. They carry no population-level uncertainty estimate. HOL maintains the benchmark and develops CIGAR. Independent reproduction is scheduled for 1.0.0. [Governance](../GOVERNANCE.md)

## Verify the numbers

With the SDK installed, run the offline receipt check from any directory.

~~~sh
cgb reference verify
~~~

The verifier checks the study and evidence digests, recomputes all 27 component values and their weighted contributions, and checks the three totals and token costs. The study design lists the registered workload, engine bindings and model settings. The receipt identifies the collection records by SHA-256 and contains the numerical inputs for verification. Raw journals with private targets and model output stay with the measurement operator. [Scorecard JSON](../src/cgb/data/reference-study.json) · [Measurement receipt](../src/cgb/data/reference-study-provenance.json) · [Study design](../src/cgb/data/reference-study-design.json)
