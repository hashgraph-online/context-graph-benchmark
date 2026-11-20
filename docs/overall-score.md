# Scoring specification

The one-hour policy is `cgb-balanced-security-privacy.v3`. The 24-hour policy, `cgb-balanced-security-privacy-24h.v2`, uses the same calculation and a separate endurance requirement. Print the machine-readable definition with `cgb overall policy`, or select a policy with `--id`.

## Overall score

Each component is a number from 0 to 100. Multiply it by its weight and add the nine products. The reported contributions are in points out of 100. Calculations use full precision. Tables round only for display.

| Component | Weight | What earns credit |
| --- | ---: | --- |
| Evidence retrieval | 10% | Recall of labeled evidence units. A unit requires the cited source text. Average per query, then across the four domains and three budgets. |
| Graph behavior | 10% | Successful episodes, averaged equally across seven graph families at each budget. |
| Balanced answer quality | 15% | Equal weight for answerable tasks and correct abstentions, averaged over team and role clusters. |
| Safe workflow completion | 10% | Registered three-role workflows with fresh, correct results from every role. |
| Operational reliability | 10% | Correct cycles (40%), successful reader execution (20%), mandatory controls (20%), resource sample coverage (10%), and restarts plus cleanup (10%). |
| Token efficiency | 10% | Utility of total observed reader and engine tokens across routine and assurance work per fresh, safe correct answer. Anchor of 1,024 tokens. |
| Runtime efficiency | 5% | Geometric mean of two positive cost utilities, query p95 latency (75%, anchor 100 ms) and sampled peak adapter memory (25%, anchor 1,024 MiB). |
| Security | 15% | Safe, useful responses to paired cases, averaged across the registered security families. |
| Privacy | 15% | Safe, useful responses to authorized and restricted data flows, averaged across the registered privacy families. |

At each query budget, a missing required domain or graph family withholds that component. The weights for 512, 2,048 and 4,096 tokens are 25%, 50% and 25%. For the cost measures, `utility = 100 × anchor / (anchor + observed cost)`. A dispatched answer with unknown usage leaves token efficiency unmeasured. Incorrect answers retain their observed cost.

## Weights and eligibility

A one-point improvement in a 10% component adds 0.10 to the overall. If the other eight components score 100, a measured score of 0, 50 or 100 on that 10% component yields an overall score of **90**, **95** or **100**, respectively.

An overall score can combine strong and weak components. Inspect the individual scores that matter for your workload. Eligibility is evaluated separately. A missing component yields an incomplete result with no total. A critical violation, a failed mandatory control, incomplete isolation or a short soak blocks the total. Coverage is the sum of measured weights, so a measured zero counts toward coverage. [Measurement and gate implementation](../src/cgb/macro.py) · [JRC discussion of compensation in composite indicators](https://knowledge4policy.ec.europa.eu/sites/default/files/wipo_pub_gii_2021-appendix1.pdf)

## Python API

```python
from cgb.overall import combine

components = dict(retrieval=0, graph=100, answers=100, workflow=100,
                  reliability=100, tokens=100, runtime=100, security=100, privacy=100)
assessment = combine(components, gates=[])
assert assessment["score"] == 90
assert assessment["weighted_points"]["retrieval"] == 0
```

`combine` performs the arithmetic and accepts gate outcomes from a caller. A benchmark study derives its components and gates from registered evidence. Use `cgb platform study-replay` to verify that chain.
