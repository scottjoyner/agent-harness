"""Standard failure taxonomy for real-task benchmark attempts.

The harness never invents ad-hoc failure strings. Every attempt terminates with
exactly one :class:`Outcome`, and every outcome is one of these eleven members.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict


class Outcome(str, Enum):
    """Terminal classification of a single benchmark attempt."""

    PROTOCOL_FAILURE = "PROTOCOL_FAILURE"
    GROUNDING_FAILURE = "GROUNDING_FAILURE"
    EMPTY_OUTPUT = "EMPTY_OUTPUT"
    TRUNCATED = "TRUNCATED"
    TIMEOUT = "TIMEOUT"
    INVALID_PATCH = "INVALID_PATCH"
    PATCH_DOES_NOT_APPLY = "PATCH_DOES_NOT_APPLY"
    TARGETED_TEST_FAILURE = "TARGETED_TEST_FAILURE"
    REGRESSION_FAILURE = "REGRESSION_FAILURE"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    SUCCESS = "SUCCESS"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Deterministic evaluation order used when an attempt trips several conditions.
#: The first matching entry wins, so a run is reproducible regardless of the
#: order in which checks happened to execute.
#:
#: The ordering is by specificity, not by the order the outcomes are enumerated
#: in :class:`Outcome`. Concretely: a patch-mechanics failure is reported ahead
#: of a grounding failure, because "you did not produce a usable diff" is the
#: actionable fact and "you did not engage with the source" is a quality signal
#: that would only obscure it. Grounding still outranks test failures, so an
#: ungrounded answer is never quietly graded as if it were a real attempt.
OUTCOME_PRECEDENCE = (
    Outcome.SOURCE_MISMATCH,
    Outcome.TIMEOUT,
    Outcome.EMPTY_OUTPUT,
    Outcome.TRUNCATED,
    Outcome.PROTOCOL_FAILURE,
    Outcome.INVALID_PATCH,
    Outcome.PATCH_DOES_NOT_APPLY,
    Outcome.GROUNDING_FAILURE,
    Outcome.TARGETED_TEST_FAILURE,
    Outcome.REGRESSION_FAILURE,
    Outcome.REVIEW_REJECTED,
    Outcome.SUCCESS,
)

_EXPLANATIONS: Dict[Outcome, str] = {
    Outcome.PROTOCOL_FAILURE: "Model output could not be parsed into the role schema.",
    Outcome.GROUNDING_FAILURE: "Model did not ground its answer in the bound source files.",
    Outcome.EMPTY_OUTPUT: "Model returned no usable content.",
    Outcome.TRUNCATED: "Model output ended early (finish_reason=length or unterminated structure).",
    Outcome.TIMEOUT: "Model call exceeded its configured deadline.",
    Outcome.INVALID_PATCH: "Candidate patch text was not a well-formed unified diff.",
    Outcome.PATCH_DOES_NOT_APPLY: "Candidate patch parsed but did not apply to the bound source.",
    Outcome.TARGETED_TEST_FAILURE: "Allow-listed targeted acceptance commands did not all pass.",
    Outcome.REGRESSION_FAILURE: "Broader acceptance commands regressed after the patch.",
    Outcome.SOURCE_MISMATCH: "Source binding verification failed; the benchmark refused to run.",
    Outcome.REVIEW_REJECTED: "Reviewer rejected the candidate and no refinement budget remained.",
    Outcome.SUCCESS: "Candidate satisfied targeted acceptance and produced no recorded regression.",
}


def explain(outcome: Outcome) -> str:
    """Human-readable one-line meaning of ``outcome``."""
    return _EXPLANATIONS[Outcome(outcome)]


def is_failure(outcome: Outcome) -> bool:
    """True unless ``outcome`` is :data:`Outcome.SUCCESS`."""
    return Outcome(outcome) is not Outcome.SUCCESS


def first_matching(outcomes) -> Outcome:
    """Reduce a collection of observed outcomes to one, by precedence order.

    Unknown/empty input yields :data:`Outcome.SUCCESS` only when nothing was
    observed; callers that must fail closed should check for emptiness first.
    """
    observed = {Outcome(o) for o in outcomes}
    if not observed:
        return Outcome.SUCCESS
    for candidate in OUTCOME_PRECEDENCE:
        if candidate in observed:
            return candidate
    return Outcome.SUCCESS
