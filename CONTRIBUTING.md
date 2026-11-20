# Contributing

Install the locked dependencies and the SDK as described in the README. Prepare the tokenizer in the checkout, then run the test and reference checks.

```sh
cgb tokenizer prepare --output .cgb/tokenizer
python -m unittest discover -s tests -v
cgb reference verify
python scripts/check_publication.py
```

For an engine integration, submit a JSONL descriptor and conformance evidence. Describe native and adapter-provided behavior, model calls, filtering, packing and process lifecycle. Include observed failures, unsupported operations and the exact engine artifacts. [Protocol](docs/adapter-contract.md) · [Submission template](examples/submission-disclosure.md)

For a new data-only test pack, include the case manifest, license, attribution, validation and replay results. Changes to a scored profile need a versioned method proposal. [Test pack contract](docs/test-pack-spec.md) · [Governance](GOVERNANCE.md)

## Preparing a release

Build the wheel and source archive with `python -m hatchling build`. Install the wheel in a fresh environment with the locked dependencies and run `cgb reference verify`. Export the reference dashboard from an empty benchmark workspace to keep local runs out of the release.

Attach both distributions, the offline dashboard, the reference scorecard, the result, measurement receipt, study design and a `SHA256SUMS` inventory. The release workflow checks the filenames and hashes, installs the attached wheel and verifies the scores. [Release verification workflow](.github/workflows/release-readback.yml)
