"""Single-attempt vs swarm-attempt comparison artifact.

This module emits *evidence about a difference*, not a verdict about which
strategy is better. A single task cannot establish that role separation helps,
and the artifact says so explicitly rather than letting a favourable delta read
as a generalisation.

Every comparison is component-to-component. There is no composite quality score,
because the components do not share a scale: a patch that applies cleanly and a
review that catches a defect are not commensurable quantities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .metrics import AttemptMetrics
from .roles import Role
from .taxonomy import Outcome
from .version import SCHEMA_COMPARISON

#: Components compared side by side. Absence is reported, never imputed.
QUALITY_COMPONENTS = (
    "outcome",
    "task_success",
    "grounding_score",
    "grounding_expected_overlap",
    "hallucinated_file_count",
    "patch_extracted",
    "patch_applied",
    "syntax_ok",
    "targeted_tests_passed",
    "targeted_tests_total",
    "broader_tests_passed",
    "broader_tests_total",
    "review_defects_total",
    "review_blocking_defects",
    "review_verdict",
    "review_missing_coverage",
    "review_contract_violations",
    "unnecessary_changed_file_count",
    "refinements_used",
)

COST_COMPONENTS = (
    "model_calls",
    "model_wall_s",
    "harness_overhead_s",
    "total_wall_s",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
)


def _component_values(metrics: AttemptMetrics) -> Dict[str, Any]:
    grounding = metrics.grounding
    patch = metrics.patch
    tests = metrics.tests
    review = metrics.review
    return {
        "outcome": metrics.outcome.value,
        "task_success": metrics.task_success,
        "grounding_score": grounding.score,
        "grounding_expected_overlap": grounding.expected_overlap,
        "hallucinated_file_count": len(grounding.hallucinated_files),
        "patch_extracted": patch.extracted,
        "patch_applied": patch.applied,
        "syntax_ok": patch.syntax_ok,
        "targeted_tests_passed": len([c for c in tests.targeted if c.passed]),
        "targeted_tests_total": len(tests.targeted),
        "broader_tests_passed": len([c for c in tests.broader if c.passed]),
        "broader_tests_total": len(tests.broader),
        "review_defects_total": len(review.defects),
        "review_blocking_defects": len(review.blocking_defects),
        "review_verdict": review.verdict,
        "review_missing_coverage": len(review.missing_coverage),
        "review_contract_violations": len(review.contract_violations),
        "unnecessary_changed_file_count": len(patch.unnecessary_changed_files),
        "refinements_used": metrics.refinements_used,
    }


def _cost_values(metrics: AttemptMetrics) -> Dict[str, Any]:
    return {
        "model_calls": metrics.model_calls,
        "model_wall_s": metrics.model_wall_s,
        "harness_overhead_s": round(metrics.harness_overhead_s, 6),
        "total_wall_s": metrics.total_wall_s,
        "prompt_tokens": metrics.prompt_tokens,
        "completion_tokens": metrics.completion_tokens,
        "total_tokens": metrics.total_tokens,
        "roles": [c.role.value for c in metrics.calls],
        "ttfts_s": [c.ttft_s for c in metrics.calls],
    }


def _delta(single: Dict[str, Any], swarm: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, swarm_value in swarm.items():
        single_value = single.get(key)
        if isinstance(single_value, bool) or isinstance(swarm_value, bool):
            out[key] = {
                "single": single_value,
                "swarm": swarm_value,
                "delta": None,
                "comparable": False,
            }
            continue
        if isinstance(single_value, (int, float)) and isinstance(swarm_value, (int, float)):
            out[key] = {
                "single": single_value,
                "swarm": swarm_value,
                "delta": round(float(swarm_value) - float(single_value), 6),
                "comparable": True,
            }
            continue
        out[key] = {
            "single": single_value,
            "swarm": swarm_value,
            "delta": None,
            "comparable": False,
            "note": "categorical component; compare values directly",
        }
    return out


def bugs_caught_by_reviewer(swarm: AttemptMetrics) -> Dict[str, Any]:
    """What the review stage contributed, stated without inflation.

    ``blocking_defects`` are findings the reviewer raised against the candidate
    the harness had already applied. ``refinement_issued`` says whether the
    bounded refinement budget was spent. ``defects_still_unaddressed`` is
    populated only when a refinement ran and validation still failed.
    """
    review = swarm.review
    targeted_ok = swarm.tests.targeted_all_passed
    return {
        "review_ran": review.ran,
        "verdict": review.verdict,
        "defects_total": len(review.defects),
        "blocking_defects": len(review.blocking_defects),
        "missing_coverage_count": len(review.missing_coverage),
        "contract_violation_count": len(review.contract_violations),
        "refinement_issued": swarm.refinements_used > 0,
        "refinement_budget": swarm.refinement_budget,
        "unaddressed_after_refinement": list(review.unaddressed_after_refinement),
        "final_targeted_passed": targeted_ok,
        "interpretation_note": (
            "A reviewer finding is evidence that the defect was legible in the "
            "candidate. It is not, by itself, evidence that the swarm produced a "
            "better patch. Whether it changed the outcome is recorded by "
            "'refinement_issued' and 'final_targeted_passed'."
        ),
    }


def build_comparison(
    task_id: str,
    task_family: str,
    fixture_sha256: str,
    singles: Sequence[AttemptMetrics],
    swarm: Optional[AttemptMetrics],
    *,
    integration_overhead_s: float = 0.0,
    integration_notes: Sequence[str] = (),
) -> Dict[str, Any]:
    """Build the single-vs-swarm comparison artifact.

    ``singles`` may hold several attempts; the "best single" selection rule is
    explicit and recorded in the artifact so the choice is auditable rather
    than implied.
    """
    best_single, selection_rule = _select_best_single(singles)
    payload: Dict[str, Any] = {
        "schema": SCHEMA_COMPARISON,
        "task_id": task_id,
        "task_family": task_family,
        "fixture_sha256": fixture_sha256,
        "single_attempts_considered": [
            {"attempt_id": m.attempt_id, "outcome": m.outcome.value} for m in singles
        ],
        "best_single_selection_rule": selection_rule,
        "best_single": None,
        "swarm": None,
        "quality_difference": {},
        "cost_difference": {},
        "reviewer_contribution": None,
        "controller_integration_overhead": {
            "harness_owned_seconds": round(integration_overhead_s, 6),
            "definition": (
                "Wall-clock time inside the harness that is not attributable to a "
                "model call: fixture loading, source binding, worktree creation, "
                "patch application, and allow-listed test execution. Excludes any "
                "orchestration performed by an external controller."
            ),
            "notes": list(integration_notes),
        },
        "scope_limits": [
            "One task is not evidence that role separation beats a single attempt.",
            "No component is aggregated into a single quality score.",
            "Token counts are null when the endpoint did not report usage; they "
            "are never estimated.",
            "This artifact records evidence only. Deciding what to do with it "
            "belongs to an authoritative controller.",
        ],
    }

    if best_single is None:
        payload["best_single_selection_rule"] = "no single attempt was available to compare"
    else:
        payload["best_single"] = {
            "attempt_id": best_single.attempt_id,
            "quality": _component_values(best_single),
            "cost": _cost_values(best_single),
        }

    if swarm is None:
        payload["swarm"] = None
    else:
        payload["swarm"] = {
            "attempt_id": swarm.attempt_id,
            "quality": _component_values(swarm),
            "cost": _cost_values(swarm),
        }
        payload["reviewer_contribution"] = bugs_caught_by_reviewer(swarm)

    if best_single is not None and swarm is not None:
        payload["quality_difference"] = {
            "components": _delta(
                _component_values(best_single), _component_values(swarm)
            ),
            "composite_score": None,
            "composite_score_note": (
                "Intentionally absent. Components are on different scales and are "
                "compared individually."
            ),
        }
        payload["cost_difference"] = _delta(_cost_values(best_single), _cost_values(swarm))

    return payload


def _select_best_single(
    singles: Sequence[AttemptMetrics],
) -> Tuple[Optional[AttemptMetrics], str]:
    if not singles:
        return None, "no single attempt was available to compare"
    success_rank = {
        Outcome.SUCCESS: 0,
        Outcome.REVIEW_REJECTED: 1,
        Outcome.REGRESSION_FAILURE: 2,
        Outcome.TARGETED_TEST_FAILURE: 3,
        Outcome.GROUNDING_FAILURE: 4,
        Outcome.INVALID_PATCH: 5,
        Outcome.PATCH_DOES_NOT_APPLY: 6,
        Outcome.PROTOCOL_FAILURE: 7,
        Outcome.TRUNCATED: 8,
        Outcome.EMPTY_OUTPUT: 9,
        Outcome.TIMEOUT: 10,
        Outcome.SOURCE_MISMATCH: 11,
    }

    def key(metrics: AttemptMetrics):
        return (
            success_rank.get(metrics.outcome, 99),
            -(metrics.tests.targeted_all_passed and metrics.patch.applied),
            -len([c for c in metrics.tests.targeted if c.passed]),
            metrics.total_wall_s,
        )

    ordered = sorted(singles, key=key)
    return ordered[0], (
        "lowest failure-rank first, then patch-applied, then targeted passes, "
        "then lowest wall time"
    )
