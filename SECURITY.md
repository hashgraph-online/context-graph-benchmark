# Security

Report a suspected vulnerability privately through GitHub's private vulnerability reporting when available, or the security contact published by HOL at https://hol.org. Do not include credentials or private context in a public issue.

The evaluator executes locally selected engine adapters. A child process, clean working directory, allowlisted environment, timeouts and bounded logs are **not an operating-system sandbox**. Run untrusted engines in a disposable VM/container with no secrets, restricted networking, read-only inputs and external resource limits. Public fixture labels are not protected against a malicious process that can read the host filesystem.

The dashboard binds only to loopback, checks Host and Origin, requires a per-process CSRF token for writes, serves assets locally and displays artifacts as text. Keep it local. It is not an authenticated multi-user execution service. It accepts registered run settings, not arbitrary browser shell commands.

Replay verifies bounded, sealed data and never executes bundled evaluators or adapter commands. A digest proves integrity against a recorded manifest, not independent origin or correctness. Do not treat a self-generated seal as a trusted signature.

Engine bindings, tokenizer caches, local readers, logs and run artifacts are ignored by Git. Audit a bundle for sensitive prompts, paths and outputs before sharing it. Tokenizer preparation and engine installation are explicit network operations. Telemetry is disabled for the LeanCTX adapter; this is not a network sandbox.

The 24-hour soak is an endurance observation under a named workload, not a penetration test, compliance certification, or proof of production security.
