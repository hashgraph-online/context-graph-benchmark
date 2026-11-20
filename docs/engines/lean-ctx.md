# LeanCTX CLI adapter

The reference study used LeanCTX 3.12.4 with the `leanctx-native-bm25-reference-closure.v1` adapter. Install the platform executable, then bind its version and digest to the workspace. [Upstream release](https://github.com/yvgude/lean-ctx/releases/tag/v3.12.4)

```sh
cgb platform bind-lean-ctx --executable /absolute/path/to/lean-ctx --output .cgb/engines/lean-ctx.json
cgb platform compare --engines lean-ctx networkx
```

For each query the adapter stages the admitted document texts under opaque filenames in a fresh project. It calls `ctx_search` with the original query, semantic action, BM25 mode and `top_k=1000`. The CLI ranks the files. The adapter maps native result headers back to documents and applies relationship closure, source citations and budget packing. An empty native result supplies no file matches. Index and CLI startup are part of query latency.

The reference study covers this file-search configuration. Dense retrieval, compression and other LeanCTX modes require their own adapter identity and measurements. The benchmark distributes the wrapper. Obtain the runtime and its license from the project. [Upstream license](https://github.com/yvgude/lean-ctx/blob/main/LICENSE.md) · [Measured results](../reference-study.md)
