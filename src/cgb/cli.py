"""Command entry point; heavyweight/optional code is loaded only by its command."""

import argparse
import sys

from . import __version__
from .jsonio import Invalid, encoded, read_json, write_json


def parser():
    root = argparse.ArgumentParser(prog="cgb", description="Engine-agnostic context benchmark")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("schemas", help="validate or export v1 schemas")
    sub = schema.add_subparsers(dest="schema_command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--output", required=True)
    check = sub.add_parser("validate")
    check.add_argument("kind")
    check.add_argument("input")
    tokenizer = commands.add_parser("tokenizer", help="explicit vocabulary acquisition/check")
    sub = tokenizer.add_subparsers(dest="tokenizer_command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--from-file")
    inspect = sub.add_parser("check")
    inspect.add_argument("directory")
    example = commands.add_parser("init", aliases=["init-example"], help="create a benchmark workspace with public suites and adapters")
    example.add_argument("--output", required=True)
    plan = commands.add_parser("plan", help="freeze a complete execution matrix")
    plan.add_argument("--suite", required=True)
    plan.add_argument("--adapters", nargs="+", required=True)
    plan.add_argument("--tokenizer", default=".cgb/tokenizer")
    plan.add_argument("--seed", type=int, default=0)
    plan.add_argument("--budgets", type=int, nargs="+")
    plan.add_argument("--timeout-ms", type=int, default=5000)
    plan.add_argument("--replicates", type=int, default=1)
    plan.add_argument("--sampling-unit", choices=["task-cluster", "process-cohort"], default="task-cluster")
    plan.add_argument("--output", required=True)
    run = commands.add_parser("run", help="execute original primary attempts")
    choice = run.add_mutually_exclusive_group(required=True)
    choice.add_argument("--plan")
    choice.add_argument("--suite")
    run.add_argument("--adapter", nargs="+")
    run.add_argument("--tokenizer", default=".cgb/tokenizer")
    run.add_argument("--output", required=True)
    run.add_argument("--progress", action="store_true", help="emit JSONL attempt progress")
    for name in ("score", "replay", "export"):
        command = commands.add_parser(name)
        command.add_argument("--run", required=True)
        if name == "score":
            command.add_argument("--labels", required=True)
        else:
            command.add_argument("--report", required=True)
        if name != "replay":
            command.add_argument("--output", required=True)
    doctor = commands.add_parser("doctor", help="check configuration without running a measured engine")
    doctor.add_argument("--plan")
    doctor.add_argument("--adapter")
    doctor.add_argument("--tokenizer", default=".cgb/tokenizer")
    adapter = commands.add_parser("adapter-check", help="exercise the adapter protocol on a tiny public input")
    adapter.add_argument("--adapter", required=True)
    adapter.add_argument("--tokenizer", default=".cgb/tokenizer")
    adapter.add_argument("--output", required=True)
    cigar = commands.add_parser("configure-cigar", help="bind an explicitly supplied SDK parent and native worker")
    cigar.add_argument("--sdk-root", required=True)
    cigar.add_argument("--worker", required=True)
    cigar.add_argument("--output", required=True)
    models = commands.add_parser("models", help="bind or inspect a local model reader")
    sub = models.add_subparsers(dest="model_command", required=True)
    bind = sub.add_parser("bind-mlx", help="hash an explicitly mounted model; download no weights")
    for flag in ("model-path", "python", "id", "model", "revision", "output"):
        bind.add_argument("--" + flag, required=True)
    bind.add_argument("--temperature", type=float, default=0)
    bind.add_argument("--max-tokens", type=int, default=128)
    probe = sub.add_parser("probe", help="list models at an explicit local endpoint")
    probe.add_argument("--backend", choices=["ollama", "openai-compatible"], required=True)
    probe.add_argument("--base-url", required=True)
    probe.add_argument("--output", required=True)
    overall = commands.add_parser("overall", help="inspect the registered composite scoring policy")
    sub = overall.add_subparsers(dest="overall_command", required=True)
    item = sub.add_parser("policy")
    item.add_argument("--id", dest="policy_id", help="Exact policy ID; defaults to the current nine-factor policy")
    graph = commands.add_parser("graph-bind", help="freeze an installed optional graph library")
    graph.add_argument("--library", choices=["networkx"], required=True)
    graph.add_argument("--descriptor", required=True)
    graph.add_argument("--output", required=True)
    packs = commands.add_parser("test-pack", help="data-only Security/Privacy and community execution packs")
    sub = packs.add_subparsers(dest="pack_command", required=True)
    for name in ("list", "validate", "conformance", "register", "scaffold", "run", "replay"):
        item = sub.add_parser(name)
        item.add_argument("--root", default=".")
        if name in ("validate", "conformance", "register"):
            item.add_argument("manifest")
        if name == "scaffold":
            item.add_argument("--id", required=True)
            item.add_argument("--output", required=True)
        if name == "run":
            item.add_argument("--engines", nargs="+", required=True)
            item.add_argument("--reader", required=True)
            item.add_argument("--packs", nargs="+")
            item.add_argument("--isolation", choices=["auto", "macos", "bubblewrap", "process"], default="auto")
            item.add_argument("--baseline")
            item.add_argument("--output")
        if name == "replay":
            item.add_argument("run")
    platform = commands.add_parser("platform", help="HOL platform: engines, comparisons and portable 1h/24h agent soaks")
    sub = platform.add_subparsers(dest="platform_command", required=True)
    inventory = sub.add_parser("engines")
    inventory.add_argument("--root", default=".")
    register = sub.add_parser("register")
    register.add_argument("--root", default=".")
    register.add_argument("--id", required=True)
    register.add_argument("--descriptor", required=True)
    lean = sub.add_parser("bind-lean-ctx")
    lean.add_argument("--executable", required=True)
    lean.add_argument("--output", required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("--root", default=".")
    compare.add_argument("--engines", nargs="+", required=True)
    compare.add_argument("--profiles", nargs="+", choices=["evidence", "graph"], default=["evidence", "graph"])
    compare.add_argument("--budgets", nargs="+", type=int, default=[512, 2048, 4096])
    compare.add_argument("--replicates", type=int, default=1)
    compare.add_argument("--name", default="Context graph comparison")
    compare.add_argument("--output")
    soak = sub.add_parser("soak")
    soak.add_argument("--root", default=".")
    soak.add_argument("--engines", nargs="+", required=True)
    soak.add_argument("--hours", type=int, choices=[1, 24], default=1)
    soak.add_argument("--pilot-seconds", type=int, help="5–120 seconds per engine; diagnostic only, never endurance-qualified")
    soak.add_argument("--reader", help="project-relative local model descriptor; no model calls without it")
    soak.add_argument("--baseline", help="selected reference engine for observed tokens saved per correct answer")
    soak.add_argument("--output")
    soak.add_argument("--assurance", action="store_true", help="separate profile: paired Security/Privacy events every 300 seconds")
    soak.add_argument("--isolation", choices=["auto", "macos", "bubblewrap", "process"], default="auto")
    soak_replay = sub.add_parser("soak-replay")
    soak_replay.add_argument("run")
    study = sub.add_parser("study", help="prospective complete query + Security/Privacy endurance cohort")
    study.add_argument("--root", default=".")
    study.add_argument("--engines", nargs="+", required=True)
    study.add_argument("--reader", required=True)
    study.add_argument("--hours", type=int, choices=[1, 24], default=1)
    study.add_argument("--pilot-seconds", type=int)
    study.add_argument("--baseline")
    study.add_argument("--isolation", choices=["auto", "macos", "bubblewrap", "process"], default="auto")
    study.add_argument("--output")
    study_replay = sub.add_parser("study-replay")
    study_replay.add_argument("run")
    compare_replay = sub.add_parser("replay")
    compare_replay.add_argument("run")
    dashboard = commands.add_parser("dashboard", help="serve results locally or export an offline dashboard")
    sub = dashboard.add_subparsers(dest="dashboard_command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--root", default=".")
    serve.add_argument("--port", type=int, default=8765)
    snapshot = sub.add_parser("export")
    snapshot.add_argument("--root", default=".")
    snapshot.add_argument("--output", required=True)
    reference = commands.add_parser("reference", help="verify the packaged scorecard against its measurement receipt")
    reference.add_subparsers(dest="reference_command", required=True).add_parser("verify", help="recalculate all three published scorecards offline")

    return root


def dispatch(args):
    from pathlib import Path
    if args.command == "test-pack":
        from .test_packs import registry
        if args.pack_command == "list":
            return registry.catalogue(args.root)
        if args.pack_command == "scaffold":
            return registry.scaffold(args.id, args.output)
        if args.pack_command == "register":
            return registry.register(args.root, args.manifest)
        if args.pack_command in ("validate", "conformance"):
            pack = registry.validate_pack(read_json(args.manifest, 1024**2))
            if args.pack_command == "conformance":
                from .test_packs.conformance import check
                return check(pack)
            return {"valid": True, "reference": registry.reference(pack), "executed": False}
        from .test_packs.runtime import benchmark, replay
        if args.pack_command == "replay":
            return replay(args.run)
        return benchmark(args.root, args.engines, args.reader, args.packs, args.isolation, args.baseline, args.output)
    if args.command == "platform":
        from .platform import engines, register
        if args.platform_command == "engines":
            return {"engines": engines(args.root)}
        if args.platform_command == "register":
            return register(args.root, args.id, args.descriptor)
        if args.platform_command == "bind-lean-ctx":
            from .leanctx import configure
            return configure(args.executable, args.output)
        if args.platform_command == "compare":
            from .comparison import compare
            import signal
            def interrupt_compare(signum, frame):
                raise KeyboardInterrupt
            previous = signal.signal(signal.SIGTERM, interrupt_compare)
            try:
                return compare(args.root, args.engines, args.name, args.profiles, args.budgets, args.replicates, args.output)
            finally:
                signal.signal(signal.SIGTERM, previous)
        if args.platform_command == "replay":
            from .comparison import replay_comparison
            return replay_comparison(args.run)
        if args.platform_command in ("study", "study-replay"):
            from .macro import benchmark as study, replay as replay_study
            if args.platform_command == "study-replay":
                return replay_study(args.run)
            return study(args.root, args.engines, args.reader, args.hours, args.pilot_seconds, args.baseline, args.isolation, args.output)
        from .portable_soak import benchmark, replay as replay_soak
        if args.platform_command == "soak-replay":
            return replay_soak(args.run)
        return benchmark(args.root, args.engines, args.hours, args.pilot_seconds, args.reader, args.baseline, args.output, args.assurance, args.isolation)
    if args.command == "overall":
        from .overall import policy
        return policy(args.policy_id)
    if args.command == "reference":
        from .reference import verify
        result = verify()
        return {"study": result["id"], "policy": result["metric"], "verified": True,
                "scores": {row["id"]: row["score"] for row in result["systems"]}}
    if args.command == "graph-bind":
        from .graph_backends import bind_library
        return bind_library(args.library, args.descriptor, args.output)
    if args.command == "dashboard":
        if args.dashboard_command == "serve":
            from .dashboard import serve_dashboard
            return serve_dashboard(args.root, args.port)
        from .platform_dashboard import export
        return export(args.root, args.output)
    if args.command == "schemas":
        from .contracts import validate
        from .schema_defs import REGISTRY
        if args.schema_command == "export":
            write_json(args.output, REGISTRY)
            return {"output": args.output}
        validate(args.kind, read_json(args.input))
        return {"valid": True, "kind": args.kind}
    if args.command == "tokenizer":
        from .tokenization import Counter, prepare
        if args.tokenizer_command == "prepare":
            return prepare(args.output, args.from_file)
        return Counter(args.directory).identity
    if args.command in ("init", "init-example"):
        from .workspace import initialize
        return initialize(args.output)
    if args.command == "configure-cigar":
        from .integrations import configure_cigar
        return configure_cigar(args.sdk_root, args.worker, args.output)
    if args.command == "models":
        from .local_models import bind_mlx, probe
        if args.model_command == "probe":
            return probe(args.backend, args.base_url, args.output)
        return bind_mlx(args.model_path, args.python, args.output, args.id, args.model,
                        args.revision, args.temperature, args.max_tokens)
    if args.command in ("plan", "run", "adapter-check", "doctor"):
        from .contracts import validate
        from .runner import check_plan, make_plan, run
        from .tokenization import Counter
        if args.command == "doctor":
            identity = Counter(args.tokenizer).identity
            if args.plan:
                check_plan(read_json(args.plan))
            if args.adapter:
                descriptor = validate("adapter-descriptor", read_json(args.adapter))
                executable = sys.executable if descriptor["command"][0] == "{python}" else descriptor["command"][0]
                if not Path(executable).is_file():
                    raise Invalid("adapter executable unavailable")
            return {"doctor": "passed", "tokenizer": identity, "label_isolation": "not-certified"}
        if args.command == "plan":
            result = make_plan(args.suite, args.adapters, args.tokenizer, args.seed, args.budgets, args.timeout_ms, args.replicates, args.sampling_unit)
            write_json(args.output, result)
            return {"plan": args.output, "cells": len(result["cells"])}
        if args.command == "adapter-check":
            from .workspace import template_directory
            suite = template_directory("suites").joinpath("smoke-v1.json")
            result = make_plan(str(suite), [args.adapter], args.tokenizer, budgets=[512])
            output = run(result, args.output, args.tokenizer)
            return {**output, "note": "inspect original outcomes; a complete run is not an efficacy pass"}
        if args.plan:
            result = read_json(args.plan)
        else:
            if not args.adapter:
                raise Invalid("suite run needs --adapter")
            result = make_plan(args.suite, args.adapter, args.tokenizer)
        def progress(event):
            sys.stdout.buffer.write(encoded(event))
            sys.stdout.buffer.flush()
        return run(result, args.output, args.tokenizer, progress=progress if args.progress else None)
    if args.command in ("score", "replay", "export"):
        from .scoring import export, replay, score
        if args.command == "score":
            return score(args.run, args.labels, args.output)
        if args.command == "replay":
            return replay(args.run, args.report)
        return export(args.run, args.report, args.output)
    raise Invalid("unknown command")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        sys.stdout.buffer.write(encoded(result))
        return 0
    except (Invalid, OSError, ValueError) as exc:
        print("cgb: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("cgb: stopped; original partial attempts retained", file=sys.stderr)
        return 130
