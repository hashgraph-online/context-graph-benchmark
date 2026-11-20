# Deliberately failed execution

This authored process emits a partial line and stalls. It is a real failure fixture, not an engine treatment.

```sh
.venv/bin/cgb plan --suite suites/smoke-v1.json --adapters examples/negative/timeout-adapter.json --budgets 512 --timeout-ms 100 --output plans/my-timeout-01.json
.venv/bin/cgb run --plan plans/my-timeout-01.json --output runs/my-timeout-01
.venv/bin/cgb score --run runs/my-timeout-01 --labels datasets/synthetic/smoke-labels.json --output reports/my-timeout-01
```

Every original cell should remain timeout with failed cleanup, partial stdout, a timed describe phase, and no useful evidence. The run can be complete because all planned failures were observed; its score and compliance remain unsuccessful. It cannot replace an earlier primary engine attempt. Actual retained local results are indexed in STATUS.md.
