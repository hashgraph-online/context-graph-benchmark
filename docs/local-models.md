# Local model readers

A scored study uses one fixed reader for all selected engines. Record its model name, revision, tokenizer, settings and runtime. The study registration binds the reader and its artifacts. Routine and assurance attempts retain their first recorded response, including errors, timeouts and native usage.

## Apple Silicon with MLX

Install the model runtime in its own Python environment. The source distribution supplies the tested Apple Silicon lock. Model weights must already be available locally. [Runtime lock](../requirements/local-model-macos-arm64.lock)

```sh
python3.12 -m venv .venv-models
.venv-models/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements/local-model-macos-arm64.lock
cgb models bind-mlx --model-path /absolute/path/to/model \
  --python /absolute/path/to/.venv-models/bin/python --id fixed --model my-model \
  --revision exact-revision --temperature 0 --max-tokens 256 --output .cgb/readers/fixed.json
```

The binding hashes mounted weights and verifies them before execution. Keep descriptors local. The reference reader used a 256-token cap, seed 41, temperature zero, top-p one and thinking disabled.

## Local HTTP readers

Use a loopback Ollama or OpenAI-compatible endpoint. Set the exact model and revision in a reader descriptor, then pass its workspace-relative path to `--reader`. The SDK can probe the endpoint to record the advertised model list. [Ollama descriptor](../examples/local-models/ollama-reader.json) · [Compatible server descriptor](../examples/local-models/compatible-reader.json)

```sh
cgb models probe --backend ollama --base-url http://127.0.0.1:11434 --output model-inventory.json
```

A routine answer has 30 seconds. An assurance answer has 120 seconds. The local MLX backend can collect correlated native token usage for up to 120 additional seconds. Late text earns zero answer credit. An unavailable usage receipt withholds the cost dimension. [Endurance schedule](soak.md)
