<p align="center">
  <a href="https://hol.org/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://hol.org/brand/Logo_Whole.png">
      <img src="https://hol.org/brand/Logo_Whole_Dark.png" width="160" alt="HOL (Hashgraph Online)">
    </picture>
  </a>
</p>

<h1 align="center">Context Graph Benchmark</h1>

<p align="center">A Python SDK and CLI for testing the evidence AI agents receive and the cost of a safe, correct answer.</p>

<p align="center">
  <a href="docs/reference-study.md">Scorecard</a> ·
  <a href="#run-a-comparison">Quickstart</a> ·
  <a href="docs/overall-score.md">Scoring</a> ·
  <a href="docs/adapter-contract.md">Adapters</a>
</p>

## Reference scores

| Engine | Score / 100 | Tokens per safe correct answer |
| --- | ---: | ---: |
| CIGAR Python SDK 0.14.2 | **93.51** | 638.95 |
| NetworkX 3.7 | **90.14** | 1,094.07 |
| LeanCTX 3.12.4 | **80.64** | 626.70 |

Each engine ran 138 queries and a one-hour soak on the same host with the same reader model. The queries covered 32 retrieval tasks and 14 graph episodes at three token budgets. All nine components were measured. Token cost includes reader and engine model usage from unsuccessful attempts.

> **Study scope.** The reference study uses public fixtures. The overall formula was selected after collection. HOL maintains the benchmark and develops CIGAR. [Study conditions and disclosures](docs/reference-study.md#reading-these-results)

## What the benchmark tests

A context graph connects records an agent needs to use together. Consider a deployment and its rollback plan. If the plan changes, the agent needs evidence from the current revision. If a handoff puts the plan outside the next agent’s permissions, it should abstain when the remaining evidence cannot support an answer.

Run the same tasks across engines to compare their behavior, or across versions of one adapter to catch regressions. The SDK records the inputs and outcomes needed to replay the scores offline.

- **Evidence and graph behavior.** The evaluator checks cited text against the source and version. It tests whether required dependencies still reach the agent after an update, deletion or change in access.
- **Agent workflows.** A fixed reader model answers from the supplied context. The evaluator scores answers, abstentions and handoffs. Paired security and privacy cases check both permitted work and restricted actions.
- **Reliability and cost.** The runner records missed cycles, clean restarts, p95 query latency, sampled peak adapter memory and model tokens per safe correct answer.

The overall score is the sum of nine weighted components. A component worth ten points contributes eight when it scores 80/100, and zero when it scores 0/100. An overall score requires all nine measurements and passing mandatory checks. [Weights and worked example](docs/reference-study.md#component-scores)

## Run a comparison

From the source checkout on macOS or Linux, install the SDK with Python 3.12 or newer. The last command verifies the three reference scores offline.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --only-binary=:all: --require-hashes -r requirements/platform.lock
python -m pip install --no-build-isolation --no-deps .
cgb reference verify
```

To measure an engine yourself, start with the included NetworkX adapter. This query comparison needs no model. Tokenizer preparation downloads the vocabulary and checks its hash.

```sh
cgb init --output my-study
cd my-study
cgb tokenizer prepare --output .cgb/tokenizer
cgb platform compare --engines networkx --output lab/comparisons/first
cgb platform replay lab/comparisons/first
cgb dashboard serve --port 8765
```

Open `http://127.0.0.1:8765` to inspect component scores and query timings.

## Run a scored study

Register the engines and a fixed local reader in your workspace. The reference adapters use the CIGAR SDK and worker for context selection, NetworkX for traversal and the LeanCTX CLI for file search. [CIGAR setup](docs/engines/cigar.md) · [LeanCTX setup](docs/engines/lean-ctx.md) · [Reader setup](docs/local-models.md)

```sh
cgb platform study --engines cigar networkx lean-ctx --reader .cgb/readers/fixed.json \
  --baseline networkx --hours 1 --output lab/studies/first-hour
cgb platform study-replay lab/studies/first-hour
cgb dashboard export --output results.html
```

Each engine gets the full hour. Before collection, pin the engines, adapters, reader settings, budgets and scoring policy. Report what each adapter supplied, retain failed first attempts and replay the sealed run before sharing scores. [Study design](docs/methodology.md) · [Execution and replay](docs/soak.md)

## How the benchmark evolves

New engines join through a JSONL adapter. New workflows start as experimental test packs. A pack enters the core score after unaffiliated review, a versioned scoring profile and a study registered before collection. Comparative qualification also calls for held-out tasks and review by at least two unaffiliated parties. Independent reproduction is planned for 1.0.0. [Test packs](docs/test-pack-spec.md) · [Governance](GOVERNANCE.md) · [Contributing](CONTRIBUTING.md) · [Apache-2.0 license](LICENSE)
