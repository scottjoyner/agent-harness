"""Component metrics for real-task benchmark attempts.

Quality is deliberately *not* collapsed into one number. Every component that
was measured stays addressable in ``metrics.json``: grounding, patch handling,
syntax, targeted tests, broader tests, review findings, collateral file edits,
and the terminal outcome. Comparison artifacts compare components against
components.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .adapter import ChatResponse
from .evaluation import CommandResult
from .roles import Role
from .taxonomy import Outcome
from .version import SCHEMA_METRICS


@dataclass
class CallMetric:
    """One bounded model call."""

    role: Role
    identity: Dict[str, Any]
    wall_s: float
    ttft_s: Optional[float]
    tokens_per_s: Optional[float]
    prompt_tokens_per_s: Optional[float]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
    usage_reported: bool
    finish_reason: Optional[str]
    started_at: float
    stream_used: bool
    server_timings: Dict[str, Any] = field(default_factory=dict)
    raw_request_sha256: str = ""
    request_profile: Dict[str, Any] = field(default_factory=dict)
    retries: int = 0

    @classmethod
    def from_response(cls, response: ChatResponse, role: Role) -> "CallMetric":
        return cls(
            role=role,
            identity=dict(response.identity),
            wall_s=response.wall_s,
            ttft_s=response.ttft_s,
            tokens_per_s=response.tokens_per_s,
            prompt_tokens_per_s=response.prompt_tokens_per_s,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
            usage_reported=response.usage_reported,
            finish_reason=response.finish_reason,
            started_at=response.started_at,
            stream_used=response.stream_used,
            server_timings=dict(response.server_timings),
            raw_request_sha256=response.raw_request_sha256,
            request_profile=dict(response.request_profile),
            retries=response.retries,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role.value,
            "identity": self.identity,
            "wall_s": self.wall_s,
            "ttft_s": self.ttft_s,
            "tokens_per_s": self.tokens_per_s,
            "prompt_tokens_per_s": self.prompt_tokens_per_s,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "usage_reported": self.usage_reported,
            "finish_reason": self.finish_reason,
            "started_at": self.started_at,
            "stream_used": self.stream_used,
            "server_timings": self.server_timings,
            "raw_request_sha256": self.raw_request_sha256,
            "request_profile": self.request_profile,
            "retries": self.retries,
        }


@dataclass
class GroundingMetric:
    """Did the model actually engage with the bound source?"""

    expected_files: Tuple[str, ...] = ()
    mentioned_files: Tuple[str, ...] = ()
    mentioned_in_source: Tuple[str, ...] = ()
    hallucinated_files: Tuple[str, ...] = ()
    expected_overlap: Optional[float] = None
    mentions_all_expected: Optional[bool] = None
    required_files_satisfied: Optional[bool] = None
    required_answer_missing: Tuple[str, ...] = ()

    @property
    def score(self) -> Optional[float]:
        """Documented grounding scalar: overlap over expected, penalised by noise.

        ``score = expected_overlap * (1 - hallucinated_rate)``. Both halves stay
        available as separate fields; this scalar exists only for ranking runs
        and is never used as a task verdict on its own.
        """
        if self.expected_overlap is None:
            return None
        total = len(self.mentioned_in_source) + len(self.hallucinated_files)
        hallucinated_rate = (len(self.hallucinated_files) / total) if total else 0.0
        return round(self.expected_overlap * (1.0 - hallucinated_rate), 6)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expected_files": list(self.expected_files),
            "mentioned_files": list(self.mentioned_files),
            "mentioned_in_source": list(self.mentioned_in_source),
            "hallucinated_files": list(self.hallucinated_files),
            "expected_overlap": self.expected_overlap,
            "mentions_all_expected": self.mentions_all_expected,
            "required_files_satisfied": self.required_files_satisfied,
            "required_answer_missing": list(self.required_answer_missing),
            "score": self.score,
        }


@dataclass
class PatchMetric:
    """Patch production and application."""

    produced: bool = False
    extracted: bool = False
    extraction_strategy: str = ""
    safety_ok: bool = False
    safety_reason: str = ""
    applied: bool = False
    applier: str = ""
    apply_reason: str = ""
    files_changed: Tuple[str, ...] = ()
    files_declared_to_change: Tuple[str, ...] = ()
    unnecessary_changed_files: Tuple[str, ...] = ()
    syntax_ok: Optional[bool] = None
    syntax_errors: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "produced": self.produced,
            "extracted": self.extracted,
            "extraction_strategy": self.extraction_strategy,
            "safety_ok": self.safety_ok,
            "safety_reason": self.safety_reason,
            "applied": self.applied,
            "applier": self.applier,
            "apply_reason": self.apply_reason,
            "files_changed": list(self.files_changed),
            "files_declared_to_change": list(self.files_declared_to_change),
            "unnecessary_changed_files": list(self.unnecessary_changed_files),
            "syntax_ok": self.syntax_ok,
            "syntax_errors": self.syntax_errors,
        }


@dataclass
class TestMetric:
    """Allow-listed acceptance evidence."""

    targeted: List[CommandResult] = field(default_factory=list)
    broader: List[CommandResult] = field(default_factory=list)

    @property
    def targeted_all_passed(self) -> bool:
        return bool(self.targeted) and all(c.passed for c in self.targeted)

    @property
    def broader_all_passed(self) -> bool:
        return bool(self.broader) and all(c.passed for c in self.broader)

    @property
    def targeted_failed(self) -> List[CommandResult]:
        return [c for c in self.targeted if not c.passed]

    @property
    def broader_failed(self) -> List[CommandResult]:
        return [c for c in self.broader if not c.passed]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "targeted_total": len(self.targeted),
            "targeted_passed": len([c for c in self.targeted if c.passed]),
            "targeted_all_passed": self.targeted_all_passed,
            "targeted": [c.to_dict() for c in self.targeted],
            "broader_total": len(self.broader),
            "broader_passed": len([c for c in self.broader if c.passed]),
            "broader_all_passed": self.broader_all_passed if self.broader else None,
            "broader": [c.to_dict() for c in self.broader],
        }


@dataclass
class ReviewMetric:
    """Reviewer findings, retained verbatim."""

    ran: bool = False
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    defects: Tuple[Dict[str, str], ...] = ()
    missing_coverage: Tuple[str, ...] = ()
    contract_violations: Tuple[str, ...] = ()
    unaddressed_after_refinement: Tuple[str, ...] = ()

    @property
    def blocking_defects(self) -> Tuple[Dict[str, str], ...]:
        return tuple(
            d for d in self.defects if str(d.get("severity", "")).lower() in ("blocker", "critical", "high")
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ran": self.ran,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "defects": [dict(d) for d in self.defects],
            "blocking_defect_count": len(self.blocking_defects),
            "missing_coverage": list(self.missing_coverage),
            "contract_violations": list(self.contract_violations),
            "unaddressed_after_refinement": list(self.unaddressed_after_refinement),
        }


#: The candidate under test was produced by a model role.
CANDIDATE_SOURCE_MODEL = "model"

#: The candidate under test was supplied by an operator via ``--patch-file``. An
#: attempt with this origin says nothing about the model's ability, and every
#: artifact that reports it says so.
CANDIDATE_SOURCE_OPERATOR = "operator_patch_file"

CANDIDATE_SOURCES = (CANDIDATE_SOURCE_MODEL, CANDIDATE_SOURCE_OPERATOR)


@dataclass
class AttemptMetrics:
    """One attempt (``single`` or ``swarm``) on one task."""

    attempt_id: str
    strategy: str
    outcome: Outcome
    calls: List[CallMetric] = field(default_factory=list)
    grounding: GroundingMetric = field(default_factory=GroundingMetric)
    patch: PatchMetric = field(default_factory=PatchMetric)
    tests: TestMetric = field(default_factory=TestMetric)
    review: ReviewMetric = field(default_factory=ReviewMetric)
    refinements_used: int = 0
    refinement_budget: int = 1
    harness_overhead_s: float = 0.0
    outcomes_seen: Tuple[Outcome, ...] = ()
    notes: List[str] = field(default_factory=list)
    #: Set when the harness itself failed unexpectedly, as distinct from anything
    #: the model did. The attempt still terminates and still writes evidence.
    harness_error: Optional[str] = None
    #: Where the candidate this attempt judged came from: ``"model"`` when a role
    #: produced it, ``"operator_patch_file"`` when an operator supplied it via
    #: ``--patch-file``.
    #:
    #: This lives on the attempt rather than on :class:`PatchMetric` because
    #: ``PatchMetric`` fields are copied by hand in ``_merge_review``, which is
    #: exactly where ``safety_ok`` and ``safety_reason`` once went missing. Attempt
    #: level placement makes it immune to that whole class of bug.
    #:
    #: Without it, an attempt a human solved by hand and one a model solved are
    #: indistinguishable in ``metrics.json``, ``comparison.json`` and the roll-up --
    #: so an operator-assisted review run reads as a model result.
    candidate_source: str = CANDIDATE_SOURCE_MODEL

    # -- aggregates, all derived, none authoritative ----------------------

    @property
    def model_calls(self) -> int:
        return len(self.calls)

    @property
    def model_wall_s(self) -> float:
        return round(sum(c.wall_s for c in self.calls), 6)

    @property
    def total_wall_s(self) -> float:
        return round(self.model_wall_s + self.harness_overhead_s, 6)

    @property
    def total_tokens(self) -> Optional[int]:
        if any(c.total_tokens is None for c in self.calls) or not self.calls:
            return None
        return sum(c.total_tokens or 0 for c in self.calls)

    @property
    def prompt_tokens(self) -> Optional[int]:
        if any(c.prompt_tokens is None for c in self.calls) or not self.calls:
            return None
        return sum(c.prompt_tokens or 0 for c in self.calls)

    @property
    def completion_tokens(self) -> Optional[int]:
        if any(c.completion_tokens is None for c in self.calls) or not self.calls:
            return None
        return sum(c.completion_tokens or 0 for c in self.calls)

    @property
    def ttfts(self) -> Tuple[Optional[float], ...]:
        return tuple(c.ttft_s for c in self.calls)

    @property
    def task_success(self) -> bool:
        return self.outcome is Outcome.SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_METRICS,
            "attempt_id": self.attempt_id,
            "strategy": self.strategy,
            "outcome": self.outcome.value,
            "task_success": self.task_success,
            "model_calls": self.model_calls,
            "model_wall_s": self.model_wall_s,
            "harness_overhead_s": round(self.harness_overhead_s, 6),
            "total_wall_s": self.total_wall_s,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "refinements_used": self.refinements_used,
            "refinement_budget": self.refinement_budget,
            "harness_error": self.harness_error,
            "candidate_source": self.candidate_source,
            "outcomes_seen": [o.value for o in self.outcomes_seen],
            "notes": list(self.notes),
            "calls": [c.to_dict() for c in self.calls],
            "grounding": self.grounding.to_dict(),
            "patch": self.patch.to_dict(),
            "tests": self.tests.to_dict(),
            "review": self.review.to_dict(),
        }


@dataclass
class TaskMetrics:
    """All attempts on one task, plus the binding identity they ran against."""

    task_id: str
    task_family: str
    fixture_sha256: str
    manifest_sha256: str
    snapshot_sha256: str
    source_binding_sha256: str
    source_head: str
    attempts: List[AttemptMetrics] = field(default_factory=list)

    def attempt(self, attempt_id: str) -> Optional[AttemptMetrics]:
        for item in self.attempts:
            if item.attempt_id == attempt_id:
                return item
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_METRICS,
            "task_id": self.task_id,
            "task_family": self.task_family,
            "fixture_sha256": self.fixture_sha256,
            "manifest_sha256": self.manifest_sha256,
            "snapshot_sha256": self.snapshot_sha256,
            "source_binding_sha256": self.source_binding_sha256,
            "source_head": self.source_head,
            "attempts": [a.to_dict() for a in self.attempts],
        }


def grounding_from_text(
    text: str,
    known_paths: Sequence[str],
    expected_files: Sequence[str],
    required_files: Sequence[str] = (),
    required_answer_contains: Sequence[str] = (),
) -> GroundingMetric:
    """Derive grounding evidence from free text.

    A "mentioned file" is any known-or-unknown repo-relative path token that
    appears verbatim in the model output. Mentioning a path that is not in the
    bound source counts as hallucination.
    """
    import re

    known = set(known_paths)
    expected = list(dict.fromkeys(expected_files))
    lowered = text.lower()

    mentioned: List[str] = []
    for candidate in sorted(known | set(expected) | set(required_files)):
        if candidate and candidate in text:
            mentioned.append(candidate)

    # Detect references to plausible-but-absent source paths.
    token_re = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|go|rs|java|rb|sh|json|yaml|yml|toml|md)\b")
    referenced = {m for m in token_re.findall(text) if "/" in m}
    hallucinated = sorted(
        p for p in referenced if p not in known and p.lstrip("./") not in known
    )

    in_source = [p for p in mentioned if p in known]
    overlap = None
    if expected:
        overlap = round(
            len([p for p in mentioned if p in expected]) / len(expected), 6
        )
    mentions_all = None
    if expected:
        mentions_all = all(p in mentioned for p in expected)

    required_ok: Optional[bool] = None
    if required_files:
        required_ok = all(path in mentioned for path in required_files)

    missing_answers: List[str] = []
    for needle in required_answer_contains:
        if needle.lower() not in lowered:
            missing_answers.append(needle)

    return GroundingMetric(
        expected_files=tuple(expected),
        mentioned_files=tuple(mentioned),
        mentioned_in_source=tuple(in_source),
        hallucinated_files=tuple(hallucinated),
        expected_overlap=overlap,
        mentions_all_expected=mentions_all,
        required_files_satisfied=required_ok,
        required_answer_missing=tuple(missing_answers),
    )


def grounding_failure(grounding: GroundingMetric) -> bool:
    """Fail-closed grounding gate.

    A response is ungrounded when it fails to engage with the bound source: it
    names none of the fixture's expected files, its overlap with them is zero,
    or it never mentions a file the fixture requires. This is the only grounding
    condition that can end an attempt; softer signals stay in the metrics.

    Missing required answer content is deliberately *not* a grounding failure:
    wrong content is a correctness problem and is reported as
    ``TARGETED_TEST_FAILURE`` via the fixture's own acceptance checks.
    """
    if grounding.required_files_satisfied is False:
        return True
    if grounding.expected_overlap is not None and grounding.expected_overlap <= 0.0:
        return True
    if grounding.expected_files and not grounding.mentioned_in_source:
        return True
    return False


def summarise_defects(reviewer_result) -> Tuple[Dict[str, str], ...]:
    return tuple(d.to_dict() for d in reviewer_result.defects)
