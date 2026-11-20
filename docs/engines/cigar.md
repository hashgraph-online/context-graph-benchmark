# CIGAR Python SDK

The reference study used the published macOS arm64 wheel for `hol-cigar==0.14.2` and its bundled worker under Python 3.14. [CIGAR release](https://github.com/hashgraph-online/hol-cigar/releases/tag/v0.14.2) · [Reference scorecard](../reference-study.md)

Install the matching SDK in an environment supported by the selected wheel. Bind the directory containing `cigar_sdk` and the distributed worker to your benchmark workspace.

```sh
cgb configure-cigar --sdk-root /absolute/path/to/sdk-parent \
  --worker /absolute/path/to/cigar-context-worker --output .cgb/engines/cigar.json
cgb platform compare --engines cigar networkx
```

`configure-cigar` records the SDK core version and SHA-256 hashes for the worker and SDK files. For each query, the adapter filters source records by scope and time, rebuilds the graph and calls `LocalContextGraph.compile` with the query and its token budget. The evaluator checks the SDK’s returned text and citations against the admitted source versions. Measured query latency includes worker startup and graph construction.
