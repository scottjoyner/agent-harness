#!/usr/bin/env python3
"""Canonical real-task benchmark entrypoint.

This is the ONE entrypoint for frozen real-task benchmarks and bounded swarm
experiments in this repository. It supersedes nothing: the historical
``fast_bench*`` / ``baremetal_bench`` / ``cpm_tb2_bench`` / ``*_harness.py``
scripts remain in place as evidence producers for their own formats (see
``docs/HARNESS-REGISTRY.md``). Use this runner when you want a reproducible
repository task, an exact source binding, role-separated attempts, and a
single-vs-swarm evidence artifact.

Examples
--------

Validate fixtures without touching any endpoint::

    python3 realtime_bench.py validate

Run one task's single attempt and swarm attempt against an already-running
OpenAI-compatible endpoint::

    python3 realtime_bench.py run \\
        --task auto_ingest_plan_shorts_live_driver \\
        --stage single --stage swarm \\
        --endpoint-config ./endpoint.json \\
        --out ./runs

Print the exact command for a given endpoint, then run it::

    python3 realtime_bench.py plan-command \\
        --task auto_ingest_plan_shorts_live_driver \\
        --base-url http://<host>:<port>/v1 --model <model-id> \\
        --label <label> --out ./runs

Nothing here discovers nodes, starts servers, claims tasks, registers providers,
or writes to any repository other than this one's ``runs/`` directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from realtask.adapter import (  # noqa: E402
    EndpointConfig,
    OpenAIChatAdapter,
    endpoint_from_env,
    endpoint_from_config_file,
)
from realtask.compare import build_comparison  # noqa: E402
from realtask.evidence import (  # noqa: E402
    RunDirectory,
    atomic_write_json,
    harness_provenance,
    source_manifest_artifact,
    utc_now,
    utc_stamp,
)
from realtask.fixtures import (  # noqa: E402
    FixtureError,
    RealTask,
    iter_fixture_manifests,
    load_source_manifest,
    load_task,
    summarize_task,
)
from realtask.runner import BenchmarkRunner, RunnerOptions  # noqa: E402
from realtask.summarize import summarize_runs  # noqa: E402
from realtask.roles import scout_result_from_evidence  # noqa: E402
from realtask.taxonomy import Outcome  # noqa: E402
from realtask.version import SCHEMA_ROLE_RESULT  # noqa: E402
from realtask.version import MAX_REFINEMENTS  # noqa: E402

DEFAULT_TASKS_ROOT = HERE / "realtask" / "tasks"
VALID_STAGES = ("single", "scout", "implement", "review", "swarm")
COMPARABLE = ("single", "swarm")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="realtime_bench.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT,
        help="directory containing frozen fixtures (default: %(default)s)",
    )
    common.add_argument(
        "--out", type=Path, default=HERE / "runs",
        help="evidence root; one timestamped directory per run (default: %(default)s)",
    )

    endpoint = argparse.ArgumentParser(add_help=False)
    endpoint.add_argument("--endpoint-config", type=Path, help="private JSON endpoint config")
    endpoint.add_argument("--base-url", help="OpenAI-compatible base URL, e.g. http://host:port/v1")
    endpoint.add_argument("--model", help="model identifier to request")
    endpoint.add_argument("--label", default="default", help="operator label used only for attribution")
    endpoint.add_argument("--node", default="", help="optional free-form node identity for evidence")
    endpoint.add_argument("--api-key-env", help="environment variable holding the API key")
    endpoint.add_argument("--timeout-s", type=float, default=120.0)
    endpoint.add_argument("--temperature", type=float, default=0.0)
    endpoint.add_argument("--seed", type=int, default=13)
    endpoint.add_argument("--max-tokens", type=int, default=1600)
    endpoint.add_argument(
        "--no-stream", action="store_true",
        help="disable streaming; TTFT is then not measurable and is recorded as null",
    )

    binding = argparse.ArgumentParser(add_help=False)
    binding.add_argument(
        "--source-root", type=Path,
        help="evaluate against this checkout instead of the fixture snapshot; "
             "HEAD and every file hash must match or the run fails with SOURCE_MISMATCH",
    )
    binding.add_argument(
        "--require-head", action="store_true",
        help="verify git HEAD even in snapshot mode (snapshot mode normally treats "
             "the recorded HEAD as provenance)",
    )

    tune = argparse.ArgumentParser(add_help=False)
    tune.add_argument("--max-source-bytes", type=int, default=262144)
    tune.add_argument("--test-timeout-s", type=float, default=300.0)
    tune.add_argument("--no-refinement", action="store_true",
                      help="forbid the single refinement round; a revise verdict becomes REVIEW_REJECTED")
    tune.add_argument("--max-refinements", type=int, default=MAX_REFINEMENTS,
                      help="refinement budget, clamped to the hard ceiling of {}".format(MAX_REFINEMENTS))

    # ---- validate ----------------------------------------------------
    validate = sub.add_parser(
        "validate", parents=[common, binding],
        help="validate every frozen fixture; no endpoint needed",
    )
    validate.add_argument("--json", action="store_true", help="emit JSON")

    # ---- list --------------------------------------------------------
    listing = sub.add_parser("list", parents=[common], help="list available fixtures")
    listing.add_argument("--json", action="store_true")

    # ---- run ---------------------------------------------------------
    run = sub.add_parser(
        "run", parents=[common, endpoint, binding, tune],
        help="run strategies against frozen fixtures",
    )
    run.add_argument("--task", action="append", default=[],
                     help="task_id; repeatable. Omit to run every fixture.")
    run.add_argument("--stage", action="append", default=[], choices=VALID_STAGES,
                     help="stage to run; repeatable. Defaults to single+swarm.")
    run.add_argument("--single-attempts", type=int, default=1,
                     help="how many single attempts to run per task (default: 1)")
    run.add_argument("--patch-file", type=Path,
                     help="candidate patch for the review stage (required with --stage review)")
    run.add_argument("--scout-file", type=Path,
                     help="a recorded scout result (scout/result.json) to hand to the "
                          "implementer instead of running a scout in this invocation")
    run.add_argument("--no-comparison", action="store_true",
                     help="skip comparison.json emission")
    run.add_argument("--run-id", help="override the generated run directory name")

    # ---- summarize ----------------------------------------------------
    summarize = sub.add_parser(
        "summarize",
        parents=[common],
        help="fold previous runs into one roll-up; needs no endpoint",
    )
    summarize.add_argument("--json", action="store_true", help="emit JSON only")
    summarize.add_argument(
        "--out-file", type=Path,
        help="where to write rollup.json (default: <out>/rollup.json)",
    )

    # ---- plan-command -------------------------------------------------
    plan = sub.add_parser(
        "plan-command", parents=[common],
        help="print the exact run command for an already-running endpoint",
    )
    plan.add_argument("--task", action="append", default=[], help="task_id; repeatable")
    plan.add_argument("--stage", action="append", default=["single", "swarm"], choices=VALID_STAGES)
    plan.add_argument("--base-url", required=True)
    plan.add_argument("--model", required=True)
    plan.add_argument("--label", default="default")

    return parser


def resolve_endpoint(args: argparse.Namespace) -> EndpointConfig:
    if getattr(args, "endpoint_config", None):
        config = endpoint_from_config_file(str(args.endpoint_config))
        overrides = {}
        if getattr(args, "base_url", None):
            overrides["base_url"] = args.base_url
        if getattr(args, "model", None):
            overrides["model"] = args.model
        if getattr(args, "label", None) and args.label != "default":
            overrides["label"] = args.label
        if getattr(args, "node", ""):
            overrides["node"] = args.node
        if getattr(args, "api_key_env", None):
            import os

            overrides["api_key"] = os.environ.get(args.api_key_env)
        if overrides:
            import dataclasses

            config = dataclasses.replace(config, **overrides)
        return config
    if getattr(args, "base_url", None) and getattr(args, "model", None):
        import os

        return EndpointConfig(
            label=args.label,
            base_url=args.base_url,
            model=args.model,
            api_key=os.environ.get(args.api_key_env) if args.api_key_env else None,
            node=args.node,
            timeout_s=args.timeout_s,
            temperature=args.temperature,
            seed=args.seed,
            max_tokens=args.max_tokens,
            stream=not args.no_stream,
        )
    try:
        return endpoint_from_env()
    except ValueError as exc:
        raise SystemExit(
            "error: no endpoint configured. Pass --endpoint-config, or both "
            "--base-url and --model, or set REALTASK_ENDPOINT_BASE_URL and "
            "REALTASK_ENDPOINT_MODEL.\n{}".format(exc)
        )


def select_tasks(args: argparse.Namespace) -> List[RealTask]:
    manifests = iter_fixture_manifests(args.tasks_root)
    if not manifests:
        raise SystemExit(
            "no fixtures found under {}".format(args.tasks_root)
        )
    if args.task:
        wanted = set(args.task)
        known = {m.parent.name for m in manifests}
        unknown = sorted(wanted - known)
        if unknown:
            raise SystemExit(
                "unknown task_id(s): {}. Available: {}".format(
                    ", ".join(unknown), ", ".join(sorted(known))
                )
            )
        manifests = [m for m in manifests if m.parent.name in wanted]
    return [load_task(m) for m in manifests]


def host_identity() -> Dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": __import__("os").cpu_count(),
    }


def command_validate(args: argparse.Namespace) -> int:
    manifests = iter_fixture_manifests(args.tasks_root)
    rows: List[Dict[str, Any]] = []
    failures = 0
    for manifest in manifests:
        try:
            task = load_task(manifest)
            load_source_manifest(task)
            from realtask.binding import verify_source_binding

            binding = verify_source_binding(
                task, require_head=getattr(args, "require_head", False)
            )
            row = summarize_task(task)
            row["binding_ok"] = binding.ok
            row["binding_head_status"] = binding.head_status
            if not binding.ok:
                row["binding_reasons"] = binding.reasons
                failures += 1
            rows.append(row)
        except FixtureError as exc:
            failures += 1
            rows.append({"task_id": manifest.parent.name, "error": str(exc), "outcome": exc.outcome.value})
    payload = {
        "command": "validate",
        "tasks_root": str(args.tasks_root),
        "count": len(rows),
        "failures": failures,
        "tasks": rows,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for row in rows:
            if "error" in row:
                print("FAIL {} [{}] {}".format(row["task_id"], row["outcome"], row["error"]))
                continue
            print(
                "{ok} {task_id}  family={family}  deliverable={deliverable}  "
                "head={head}  binding={binding}".format(
                    ok="ok  " if row["binding_ok"] else "FAIL",
                    task_id=row["task_id"],
                    family=row["task_family"],
                    deliverable=row["deliverable"],
                    head=row["head"][:12],
                    binding="ok" if row["binding_ok"] else row.get("binding_reasons"),
                )
            )
        print("{} fixture(s), {} failure(s)".format(len(rows), failures))
    return 1 if failures else 0


def command_list(args: argparse.Namespace) -> int:
    rows = []
    for manifest in iter_fixture_manifests(args.tasks_root):
        task = load_task(manifest)
        rows.append(
            {
                "task_id": task.task_id,
                "task_family": task.task_family,
                "deliverable": task.deliverable,
                "title": task.title,
                "source_files": list(task.source.paths),
            }
        )
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(
                "{task_id}  [{task_family}/{deliverable}]  {title}".format(**row)
            )
    return 0


def _load_scout_handoff(path: Path):
    """Read a recorded scout result and return ``(ScoutResult, provenance)``.

    The handoff is evidence, so it is validated as evidence: wrong schema, wrong
    shape or a missing file is an error rather than a silently ignored flag.
    """
    path = Path(path)
    if not path.is_file():
        raise SystemExit("--scout-file not found: {}".format(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit("--scout-file is not valid JSON: {}".format(exc))
    schema = payload.get("schema")
    if schema != SCHEMA_ROLE_RESULT:
        raise SystemExit(
            "--scout-file schema is {!r}; expected {!r}. Point it at a scout/result.json "
            "or swarm/result.json written by this harness.".format(schema, SCHEMA_ROLE_RESULT)
        )
    if "scout" not in payload:
        raise SystemExit(
            "--scout-file has no 'scout' block. Use the result.json from a scout run, "
            "not from a single or implementer run."
        )
    try:
        scout = scout_result_from_evidence(payload["scout"])
    except (ValueError, TypeError) as exc:
        raise SystemExit("--scout-file scout block is malformed: {}".format(exc))
    provenance = {
        "path": str(path),
        "source_attempt_id": payload.get("attempt_id"),
        "source_strategy": payload.get("strategy"),
        "source_outcome": payload.get("outcome"),
        "plan_steps": len(scout.plan),
        "relevant_files": list(scout.relevant_files),
        "confidence": scout.confidence,
    }
    return scout, provenance


def command_run(args: argparse.Namespace) -> int:
    tasks = select_tasks(args)
    stages = args.stage or list(COMPARABLE)
    for stage in stages:
        if stage not in VALID_STAGES:
            raise SystemExit("unknown stage {!r}".format(stage))

    if args.scout_file and not ({"implement", "swarm", "review"} & set(stages)):
        print(
            "warning: --scout-file only affects the implement, swarm and review stages; "
            "this run uses {}".format(", ".join(stages) or "(none)"),
            file=sys.stderr,
        )

    patch_override = None
    patch_provenance = None
    if args.patch_file:
        raw = Path(args.patch_file).read_bytes()
        patch_override = raw.decode("utf-8", errors="replace")
        # Hashed, not just named: a path can be rewritten between runs, and the
        # record has to say *which* candidate was judged after the fact.
        patch_provenance = {
            "path": str(args.patch_file),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
    elif "review" in stages:
        raise SystemExit("--stage review requires --patch-file")

    scout_override = None
    scout_handoff = None
    if args.scout_file:
        scout_override, scout_handoff = _load_scout_handoff(args.scout_file)

    config = resolve_endpoint(args)
    adapter = OpenAIChatAdapter(config)

    provenance = harness_provenance(HERE)
    run_id = args.run_id or "{}-{}".format(
        tasks[0].task_id if len(tasks) == 1 else "multi",
        utc_stamp(),
    )
    run_dir = RunDirectory(args.out, run_id)
    # Evaluation work trees are harness scratch, not evidence. Keep them out of
    # the evidence root so a directory listing there shows runs and nothing else.
    work_root = Path(args.out) / (run_id + ".work")

    options = RunnerOptions(
        max_source_bytes=args.max_source_bytes,
        test_timeout_s=args.test_timeout_s,
        allow_refinement=not args.no_refinement,
        require_head=args.require_head,
    )
    options.max_refinements = min(args.max_refinements, MAX_REFINEMENTS)

    # One task keeps the flat documented layout. Several tasks each get their own
    # subdirectory, so evidence for one can never overwrite another's.
    multi = len(tasks) > 1
    task_dirs = [
        RunDirectory(args.out, run_id, prefix="tasks/{}/".format(t.task_id)) if multi
        else run_dir
        for t in tasks
    ]
    for task, task_dir in zip(tasks, task_dirs):
        task_dir.copy_fixture(task.task_path, task.source_manifest_path)
        task_dir.write_json(
            "source-manifest.verified.json",
            source_manifest_artifact(
                load_source_manifest(task), None,
                {"note": "per-task binding verdict is in metrics.json"},
            ),
        )

    run_dir.write_run_manifest(
        harness_sha=provenance["git_sha"],
        harness_dirty=provenance["git_dirty"],
        fixture_sha256=",".join(t.fixture_sha256() for t in tasks),
        task_id=",".join(t.task_id for t in tasks),
        task_family=",".join(sorted({t.task_family for t in tasks})),
        endpoints=[config.identity()],
        strategies=stages,
        argv=sys.argv,
        host=host_identity(),
        options={
            "max_source_bytes": args.max_source_bytes,
            "test_timeout_s": args.test_timeout_s,
            "allow_refinement": not args.no_refinement,
            "max_refinements": options.max_refinements,
            "refinement_hard_ceiling": MAX_REFINEMENTS,
            "source_root": str(args.source_root) if args.source_root else None,
            "require_head": args.require_head,
            "tasks_root": str(args.tasks_root),
            "scout_handoff": scout_handoff,
            "candidate_override": patch_provenance,
        },
        started_at=utc_now(),
    )

    results = []
    integration_overhead = 0.0
    comparisons = []
    harness_errors: List[str] = []
    for task, task_dir in zip(tasks, task_dirs):
        runner = BenchmarkRunner(
            adapter,
            task_dir,
            options,
            work_root=work_root,
            harness_root=HERE,
            source_root=args.source_root,
        )
        try:
            result = runner.run_task(
                task,
                stages,
                single_attempts=args.single_attempts,
                patch_override=patch_override,
                scout_override=scout_override,
            )
        finally:
            runner.close()
        results.append(result)
        integration_overhead += sum(a.metrics.harness_overhead_s for a in result.attempts)

        for state in result.attempts:
            print(
                "{task}  {strategy:8s}  outcome={outcome:22s} calls={calls} "
                "wall={wall:.1f}s tokens={tokens}".format(
                    task=task.task_id,
                    strategy=state.strategy,
                    outcome=state.metrics.outcome.value,
                    calls=state.metrics.model_calls,
                    wall=state.metrics.total_wall_s,
                    tokens=state.metrics.total_tokens,
                )
            )
            for note in state.metrics.notes:
                print("    note: {}".format(note))
            if state.metrics.harness_error:
                harness_errors.append(
                    "{}::{}".format(task.task_id, state.strategy)
                )
                print(
                    "    HARNESS ERROR: {}".format(state.metrics.harness_error),
                    file=sys.stderr,
                )
        if not result.authoritative_source_unchanged:
            print(
                "INTEGRITY FAILURE: the fixture tree changed during the run",
                file=sys.stderr,
            )
            run_dir.finalize_manifest(
                {"integrity": {"authoritative_source_unchanged": False}}
            )
            shutil.rmtree(work_root, ignore_errors=True)
            return 2

    if not args.no_comparison:
        for task, task_dir, result in zip(tasks, task_dirs, results):
            singles = [a for a in result.attempts if a.strategy == "single"]
            swarms = [a for a in result.attempts if a.strategy == "swarm"]
            comparison = build_comparison(
                task.task_id,
                task.task_family,
                task.fixture_sha256(),
                [a.metrics for a in singles],
                swarms[0].metrics if swarms else None,
                integration_overhead_s=integration_overhead,
                integration_notes=[
                    "measured inside the harness only",
                    "external controller orchestration is not measured by this tool",
                ],
            )
            # The task directory already identifies the task, so the artifact
            # keeps the documented name in both the flat and nested layouts.
            task_dir.write_json("comparison.json", comparison)
            comparisons.append("{}/comparison.json".format(task_dir.prefix).lstrip("/"))

    run_dir.finalize_manifest(
        {
            "outcomes": {
                task.task_id: [a.metrics.outcome.value for a in result.attempts]
                for task, result in zip(tasks, results)
            },
            "integrity": {
                "authoritative_source_unchanged": all(
                    r.authoritative_source_unchanged for r in results
                ),
                "harness_errors": harness_errors,
            },
            "authority": {
                "authoritative_repo_mutated": False,
                "assistx_task_state_mutated": False,
                "routing_or_admission_mutated": False,
            },
        }
    )
    shutil.rmtree(work_root, ignore_errors=True)
    print("evidence: {}".format(run_dir.path))
    for path in comparisons:
        print("  comparison: {}".format(path))
    for note in adapter.degradations():
        print("  endpoint note: {}".format(note))
    if harness_errors:
        # Evidence was still emitted for every task. Exit 3 so a controller
        # distinguishes "the models did badly" from "the harness did badly".
        print(
            "\n{} attempt(s) hit a harness error; evidence is complete but the run "
            "is not clean. Exit 3.".format(len(harness_errors)),
            file=sys.stderr,
        )
        return 3
    return 0


def command_plan_command(args: argparse.Namespace) -> int:
    tasks = select_tasks(args)
    stages = args.stage or ["single", "swarm"]
    target = str((args.out / "runs").resolve()) if (args.out / "runs").exists() else str(args.out.resolve())
    print("# point agent-harness at an already-running endpoint and run frozen real tasks")
    print("# tasks: {}".format(", ".join(t.task_id for t in tasks)))
    print("# stages: {}".format(", ".join(stages)))
    print()
    for task in tasks:
        parts = [
            sys.executable,
            str(HERE / "realtime_bench.py"),
            "run",
            "--task",
            task.task_id,
            "--out",
            target,
            "--base-url",
            args.base_url,
            "--model",
            args.model,
            "--label",
            args.label,
        ]
        for stage in stages:
            parts.extend(["--stage", stage])
        print("cd {} && {}".format(HERE, " ".join(_quote(p) for p in parts)))
    return 0


def _quote(token: str) -> str:
    if any(ch in token for ch in " \t\"'$&|<>;"):
        return '"{}"'.format(token.replace('"', '\\"'))
    return token


def _render_rollup(rollup_dict: Dict[str, Any]) -> str:
    """Human-readable roll-up. Components stay components."""
    agg = rollup_dict["aggregate"]
    lines = []
    lines.append("=== runs ===")
    lines.append("  {}  runs={} tasks={} attempts={}".format(
        rollup_dict["runs_root"], agg["runs"], agg["tasks"], agg["attempts"]))
    if rollup_dict["harness_git_shas"]:
        lines.append("  harness git shas: {}".format(
            ", ".join(sha[:12] for sha in rollup_dict["harness_git_shas"])))
    for runtime in rollup_dict["model_runtimes"]:
        lines.append("  runtime: {} model={} node={}".format(
            runtime.get("label"), runtime.get("model"), runtime.get("node") or "-"))

    lines.append("=== outcomes ===")
    for outcome, count in agg["outcome_histogram"].items():
        lines.append("  {:22s} {}".format(outcome, count))

    lines.append("=== by task family ===")
    for family, row in agg["by_task_family"].items():
        lines.append("  {:22s} tasks={} successful_tasks={} attempts={} successes={}".format(
            family, row["tasks"], row["successful_tasks"], row["attempts"], row["successes"]))

    lines.append("=== by strategy ===")
    for strategy, row in agg["by_strategy"].items():
        lines.append("  {:22s} attempts={} successes={} model_calls={}".format(
            strategy, row["attempts"], row["successes"], row["model_calls"]))

    totals = agg["totals"]
    lines.append("=== totals ===")
    for key in ("model_calls", "model_wall_s", "harness_overhead_s",
                "prompt_tokens", "completion_tokens", "total_tokens"):
        lines.append("  {:22s} {}".format(key, totals[key]))

    if rollup_dict["harness_errors"]:
        lines.append("=== harness errors ===")
        for entry in rollup_dict["harness_errors"]:
            lines.append("  {}".format(entry))
    if rollup_dict["runs_with_integrity_failures"]:
        lines.append("=== runs with integrity failures ===")
        for entry in rollup_dict["runs_with_integrity_failures"]:
            lines.append("  {}".format(entry))
    if rollup_dict["skipped_runs"]:
        lines.append("=== skipped ===")
        for entry in rollup_dict["skipped_runs"]:
            lines.append("  {}: {}".format(entry["path"], entry["reason"]))

    lines.append("=== scope ===")
    lines.append("  composite_score: {} ({})".format(
        rollup_dict["composite_score"], "intentionally absent"))
    for limit in rollup_dict["scope_limits"]:
        lines.append("  - {}".format(limit))
    return "\n".join(lines)


def command_summarize(args: argparse.Namespace) -> int:
    rollup = summarize_runs(args.out)
    payload = rollup.to_dict()
    destination = args.out_file or (Path(args.out) / "rollup.json")
    atomic_write_json(destination, payload)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(_render_rollup(payload))
        print("rollup: {}".format(destination))
    # A roll-up with nothing in it is a configuration mistake, not a result.
    return 0 if rollup.run_ids else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return command_validate(args)
        if args.command == "list":
            return command_list(args)
        if args.command == "run":
            return command_run(args)
        if args.command == "summarize":
            return command_summarize(args)
        if args.command == "plan-command":
            return command_plan_command(args)
    except FixtureError as exc:
        print("error [{}]: {}".format(exc.outcome.value, exc), file=sys.stderr)
        return 2
    parser.error("unknown command {}".format(args.command))
    return 2


if __name__ == "__main__":
    sys.exit(main())
