"""Cross-task roll-up: the artifact a controller can actually consume.

One run produces one comparison. A qualification campaign produces many runs
across many tasks, and something has to fold them together.

This module emits evidence about a corpus of runs, in the same spirit as
:mod:`realtask.compare`: components stay components, nothing is aggregated into
a single quality number, and no qualification verdict is asserted. Deciding what
a model's results mean belongs to an authoritative controller.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .taxonomy import Outcome
from .version import SCHEMA_METRICS, SCHEMA_ROLLUP, SCHEMA_RUN_MANIFEST

#: Per-attempt fields worth carrying into the roll-up. Everything else stays
#: addressable in the source run's metrics.json.
_ATTEMPT_FIELDS = (
    "attempt_id", "strategy", "outcome", "task_success", "model_calls",
    "model_wall_s", "harness_overhead_s", "total_wall_s", "prompt_tokens",
    "completion_tokens", "total_tokens", "refinements_used", "refinement_budget",
    "harness_error", "candidate_source",
)


@dataclass
class SkippedRun:
    """A run directory that could not be read, and why."""

    path: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "reason": self.reason}


@dataclass
class Rollup:
    runs_root: str
    run_ids: List[str] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[SkippedRun] = field(default_factory=list)
    harness_shas: List[str] = field(default_factory=list)
    endpoint_identities: List[Dict[str, Any]] = field(default_factory=list)
    integrity: Dict[str, Any] = field(default_factory=dict)
    harness_errors: List[str] = field(default_factory=list)

    def _attempts_by_candidate_source(self) -> Dict[str, int]:
        """How many pooled attempts judged a model's candidate vs an operator's."""
        counts: Dict[str, int] = {}
        for task in self.tasks:
            for attempt in task["attempts"]:
                source = attempt.get("candidate_source") or "unknown"
                counts[source] = counts.get(source, 0) + 1
        return dict(sorted(counts.items()))

    def _scope_limits(self) -> List[str]:
        """The standing limits, plus one earned by what actually got pooled.

        The harness already refuses to pool across artifact *schemas*, so legacy
        ``bench_*`` runs can never be silently mixed in. It did not apply the same
        discipline across harness *revisions* within one schema, and that matters
        more than it sounds: the meaning of a component can change between two
        revisions that are both schema-compatible. Nine call sites once billed the
        model's own latency to ``harness_overhead_s``, so a roll-up spanning that
        fix sums a number that meant one thing before it and another after.

        Found by running ``summarize`` over the first real evidence directories:
        the totals carried 53.95s of harness overhead that was really model
        latency, because that run predated the fix.
        """
        limits = [
            "A roll-up describes runs that already happened. It does not establish "
            "that a model qualifies for anything.",
            "Task families are not equally hard and are not equally represented; the "
            "counts below are a record of what was run, not a difficulty weighting.",
            "Token totals are null when any attempt's endpoint omitted usage. They "
            "are never estimated and never partially summed.",
            "This artifact records evidence only. What to do with it belongs to an "
            "authoritative controller.",
        ]
        by_source = self._attempts_by_candidate_source()
        total_attempts = sum(by_source.values())
        operator = by_source.get("operator_patch_file", 0)
        if operator:
            limits.append(
                "{} of {} pooled attempt(s) judged a candidate supplied by an "
                "operator via --patch-file rather than one produced by a model. "
                "They are evidence about the fixture and the harness, not about "
                "any model's ability; aggregate.by_candidate_source separates "
                "them.".format(operator, total_attempts)
            )
        unknown = by_source.get("unknown", 0)
        if unknown:
            limits.append(
                "{} of {} pooled attempt(s) predate candidate-source "
                "provenance, so where their candidates came from is unrecorded. "
                "They are counted under 'unknown' rather than assumed to be "
                "model attempts.".format(unknown, total_attempts)
            )
        shas = sorted(set(self.harness_shas))
        if len(shas) > 1:
            limits.append(
                "These runs came from {} different harness revisions ({}). A "
                "component's meaning can change between revisions that share a "
                "schema, so the aggregate figures below record what was reported "
                "rather than a like-for-like measurement. Compare the per-run "
                "metrics.json files instead of these totals.".format(
                    len(shas), ", ".join(sha[:12] for sha in shas)
                )
            )
        return limits

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_ROLLUP,
            "runs_root": self.runs_root,
            "run_ids": list(self.run_ids),
            "harness_git_shas": sorted(set(self.harness_shas)),
            "mixed_harness_revisions": len(set(self.harness_shas)) > 1,
            "attempts_by_candidate_source": self._attempts_by_candidate_source(),
            "model_runtimes": self.endpoint_identities,
            "runs_with_integrity_failures": sorted(self.integrity.get("dirty_runs", [])),
            "harness_errors": list(self.harness_errors),
            "skipped_runs": [s.to_dict() for s in self.skipped],
            "tasks": list(self.tasks),
            "aggregate": self.aggregate(),
            "component_metrics_retained": True,
            "composite_score": None,
            "composite_score_note": (
                "Intentionally absent. The components do not share a scale: a patch "
                "that applies cleanly and a review that catches a defect are not "
                "commensurable. Compare them individually, as metrics.json does."
            ),
            "scope_limits": self._scope_limits(),
        }

    # -- aggregate ---------------------------------------------------------

    def aggregate(self) -> Dict[str, Any]:
        attempts = [t for task in self.tasks for t in task["attempts"]]
        by_outcome: Dict[str, int] = {}
        by_family: Dict[str, Dict[str, Any]] = {}
        by_strategy: Dict[str, Dict[str, Any]] = {}

        by_candidate_source: Dict[str, int] = {}
        for attempt in attempts:
            by_outcome[attempt["outcome"]] = by_outcome.get(attempt["outcome"], 0) + 1
            # An attempt whose candidate came from --patch-file was solved by an
            # operator. Counting it the same as a model's is how a hand-written
            # reference patch becomes a benchmark score.
            #
            # A null here means the artifact predates the field. That is bucketed
            # as "unknown" rather than assumed to be "model": the assumption would
            # be wrong in exactly the direction that flatters the result.
            source = attempt.get("candidate_source") or "unknown"
            by_candidate_source[source] = by_candidate_source.get(source, 0) + 1
            strat = by_strategy.setdefault(
                attempt["strategy"],
                {"attempts": 0, "successes": 0, "model_calls": 0},
            )
            strat["attempts"] += 1
            strat["successes"] += 1 if attempt["outcome"] == Outcome.SUCCESS.value else 0
            strat["model_calls"] += attempt["model_calls"] or 0

        for task in self.tasks:
            fam = by_family.setdefault(
                task["task_family"],
                {"tasks": 0, "attempts": 0, "successes": 0, "successful_tasks": 0},
            )
            fam["tasks"] += 1
            successes = 0
            for attempt in task["attempts"]:
                fam["attempts"] += 1
                if attempt["outcome"] == Outcome.SUCCESS.value:
                    fam["successes"] += 1
                    successes += 1
            if successes:
                fam["successful_tasks"] += 1

        return {
            "runs": len(self.run_ids),
            "tasks": len(self.tasks),
            "attempts": len(attempts),
            "outcome_histogram": dict(sorted(by_outcome.items())),
            "by_task_family": dict(sorted(by_family.items())),
            "by_strategy": dict(sorted(by_strategy.items())),
            "by_candidate_source": dict(sorted(by_candidate_source.items())),
            "totals": {
                "model_calls": _sum(attempts, "model_calls"),
                "model_wall_s": _sum(attempts, "model_wall_s"),
                "harness_overhead_s": _sum(attempts, "harness_overhead_s"),
                "prompt_tokens": _sum_nullable(attempts, "prompt_tokens"),
                "completion_tokens": _sum_nullable(attempts, "completion_tokens"),
                "total_tokens": _sum_nullable(attempts, "total_tokens"),
            },
        }


def _sum(attempts: Sequence[Dict[str, Any]], field_name: str) -> float:
    total = 0.0
    for attempt in attempts:
        value = attempt.get(field_name)
        if isinstance(value, (int, float)):
            total += float(value)
    return round(total, 6)


def _sum_nullable(attempts: Sequence[Dict[str, Any]], field_name: str) -> Optional[int]:
    """Sum, or ``None`` when any contributing attempt lacks the number.

    A partial token total is worse than none: it invites a reader to treat a
    truncated measurement as a complete one.
    """
    if not attempts:
        return None
    total = 0
    for attempt in attempts:
        value = attempt.get(field_name)
        if not isinstance(value, int):
            return None
        total += value
    return total


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _task_metrics_files(run_dir: Path) -> List[Path]:
    """metrics.json at the root, plus one per task for nested multi-task runs."""
    found: List[Path] = []
    root = run_dir / "metrics.json"
    if root.is_file():
        found.append(root)
    found.extend(sorted(run_dir.glob("tasks/*/metrics.json")))
    return found


def summarize_runs(runs_root: Path) -> Rollup:
    """Fold every run directory under ``runs_root`` into one roll-up.

    Unreadable or partial runs are recorded in ``skipped`` rather than raising: a
    corpus should never fail to summarise because one run was interrupted.
    """
    runs_root = Path(runs_root)
    rollup = Rollup(runs_root=str(runs_root))
    if not runs_root.is_dir():
        return rollup

    for run_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.is_file():
            # Scratch or still-running directory, not evidence.
            continue
        try:
            manifest = _load_json(manifest_path)
        except (OSError, json.JSONDecodeError) as exc:
            rollup.skipped.append(SkippedRun(str(run_dir), "unreadable manifest: {}".format(exc)))
            continue
        if manifest.get("schema") != SCHEMA_RUN_MANIFEST:
            rollup.skipped.append(
                SkippedRun(str(run_dir), "manifest schema is {!r}".format(manifest.get("schema")))
            )
            continue

        run_id = manifest.get("run_id", run_dir.name)
        rollup.run_ids.append(run_id)
        harness = manifest.get("harness") or {}
        if harness.get("git_sha"):
            rollup.harness_shas.append(harness["git_sha"])
        for endpoint in manifest.get("model_runtime", []) or []:
            if endpoint not in rollup.endpoint_identities:
                rollup.endpoint_identities.append(endpoint)
        integrity = manifest.get("integrity") or {}
        if not integrity.get("authoritative_source_unchanged", True):
            rollup.integrity.setdefault("dirty_runs", []).append(run_id)
        for attempt_id in integrity.get("harness_errors", []) or []:
            rollup.harness_errors.append(attempt_id)

        metrics_files = _task_metrics_files(run_dir)
        if not metrics_files:
            rollup.skipped.append(
                SkippedRun(str(run_dir), "no metrics.json; run produced no evidence")
            )
            continue
        for metrics_path in metrics_files:
            try:
                payload = _load_json(metrics_path)
            except (OSError, json.JSONDecodeError) as exc:
                rollup.skipped.append(
                    SkippedRun(str(metrics_path), "unreadable metrics: {}".format(exc))
                )
                continue
            if payload.get("schema") != SCHEMA_METRICS:
                rollup.skipped.append(
                    SkippedRun(str(metrics_path), "metrics schema is {!r}".format(payload.get("schema")))
                )
                continue
            rollup.tasks.append(_task_row(run_id, payload))

    rollup.harness_errors = sorted(set(rollup.harness_errors))
    return rollup


def _task_row(run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    attempts = []
    for attempt in payload.get("attempts", []) or []:
        row = {key: attempt.get(key) for key in _ATTEMPT_FIELDS}
        row["grounding_score"] = (attempt.get("grounding") or {}).get("score")
        patch = attempt.get("patch") or {}
        row["patch_applied"] = patch.get("applied")
        row["files_changed"] = len(patch.get("files_changed") or [])
        row["unnecessary_changed_files"] = len(patch.get("unnecessary_changed_files") or [])
        row["syntax_ok"] = patch.get("syntax_ok")
        tests = attempt.get("tests") or {}
        row["targeted_passed"] = tests.get("targeted_passed")
        row["targeted_total"] = tests.get("targeted_total")
        row["broader_passed"] = tests.get("broader_passed")
        row["broader_total"] = tests.get("broader_total")
        review = attempt.get("review") or {}
        row["review_verdict"] = review.get("verdict")
        row["review_defects"] = len(review.get("defects") or [])
        attempts.append(row)
    return {
        "run_id": run_id,
        "task_id": payload.get("task_id"),
        "task_family": payload.get("task_family"),
        "fixture_sha256": payload.get("fixture_sha256"),
        "source_head": payload.get("source_head"),
        "source_binding_sha256": payload.get("source_binding_sha256"),
        "attempts": attempts,
    }
