"""Optional, fixed MLX worker. Loads local safetensors only; never reads labels."""

import contextlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    sys.stdout.flush()


def main():
    path = Path(sys.argv[1])
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("mounted model directory required")
    started = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        import mlx.core as mx
        from mlx_lm import load, stream_generate
        from mlx_lm.sample_utils import make_sampler
        model, tokenizer = load(str(path), tokenizer_config={
            "trust_remote_code": False, "local_files_only": True}, trust_remote_code=False)
    emit({"ready": True, "load_seconds": time.perf_counter() - started,
          "runtime": {name: importlib.metadata.version(name) for name in
                      ("mlx-lm", "mlx", "transformers", "tokenizers")},
          "cache_policy": "fresh prompt cache for every request"})
    for line in sys.stdin.buffer:
        if len(line) > 8 * 1024 * 1024:
            raise ValueError("request byte limit")
        request = json.loads(line)
        chunks, last, input_tokens = [], None, None
        began = time.perf_counter()
        try:
            settings = request["settings"]
            with contextlib.redirect_stdout(sys.stderr):
                mx.random.seed(request["seed"])
                prompt = tokenizer.apply_chat_template(request["messages"], tokenize=True,
                    add_generation_prompt=True, enable_thinking=settings["enable_thinking"])
                input_tokens = len(prompt)
                if input_tokens + settings["max_tokens"] > request["context_window"]:
                    raise ValueError("model context window exceeded; truncation is disabled")
                # No prompt_cache is supplied: MLX creates a new cache each call.
                for last in stream_generate(model, tokenizer, prompt,
                        max_tokens=settings["max_tokens"], sampler=make_sampler(
                            temp=settings["temperature"], top_p=settings["top_p"])):
                    chunks.append(last.text)
            emit({"id": request["id"], "status": "ok", "text": "".join(chunks),
                  "input_tokens": input_tokens, "output_tokens": last.generation_tokens if last else 0,
                  "finish_reason": last.finish_reason if last else "stop",
                  "seconds": time.perf_counter() - began,
                  "peak_memory_gb": last.peak_memory if last else None})
        except Exception as error:
            emit({"id": request["id"], "status": "error", "text": "".join(chunks),
                  "input_tokens": input_tokens,
                  "output_tokens": last.generation_tokens if last else None,
                  "seconds": time.perf_counter() - began,
                  "error": type(error).__name__ + ": " + str(error)})


if __name__ == "__main__":
    main()
