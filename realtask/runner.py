"""Canonical stage orchestration.

The runner is the only component that decides what happens next. It owns:

* source binding verification, before anything else;
* the bounded sequence of role calls for each strategy;
* patch application inside disposable worktrees only;
* allow-listed test execution;
* taxonomy assignment;
* evidence emission.

It never edits the bound source, never runs a shell, never starts a server, and
never talks to a scheduler. Models receive text and return text.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import roles as role_contracts
from .adapter import AdapterError, ChatAdapter, ChatRequest
from .binding import SourceBinding, binding_prompt_block, verify_source_binding
from .evaluation import CommandResult, EvaluationWorktree
from .fixtures import RealTask
from .metrics import (
    AttemptMetrics,
    CallMetric,
    GroundingMetric,
    PatchMetric,
    ReviewMetric,
    TaskMetrics,
    TestMetric,
    grounding_failure,
    grounding_from_text,
    summarise_defects,
)
from .patch import extract_patch
from .roles import (
    ImplementerResult,
    ReviewerResult,
    Role,
    RoleProtocolError,
    ScoutResult,
)
from .taxonomy import Outcome
from .version import MAX_REFINEMENTS, SCHEMA_ROLE_RESULT, SCHEMA_TEST_RESULTS

DEFAULT_MAX_SOURCE_BYTES = 262144

#: Outcomes that describe one *candidate* rather than the attempt as a whole.
#: When the bounded refinement round produces a new candidate, these are cleared
#: from the terminal set: the previous candidate's failure is preserved in
#: ``outcomes_seen`` but no longer decides the verdict, because final validation
#: of the refined candidate is what the attempt is actually judged on.
CANDIDATE_SCOPED_OUTCOMES = frozenset(
    {
        Outcome.INVALID_PATCH,
        Outcome.PATCH_DOES_NOT_APPLY,
        Outcome.TARGETED_TEST_FAILURE,
        Outcome.REGRESSION_FAILURE,
        Outcome.REVIEW_REJECTED,
    }
)


class RefinementBudgetExhausted(RuntimeError):
    """A second refinement was requested; the harness refuses.

    The swarm stage permits at most :data:`realtask.version.MAX_REFINEMENTS`
    revision round per attempt. The budget is a hard ceiling: CLI options can
    lower it but never raise it.
    """


@dataclass
class RunnerOptions:
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
    test_timeout_s: float = 300.0
    allow_refinement: bool = True
    keep_worktrees: bool = False
    require_head: bool = False

    def clamp(self) -> "RunnerOptions":
        self.max_refinements = min(
            getattr(self, "max_refinements", MAX_REFINEMENTS), MAX_REFINEMENTS
        )
        return self

    max_refinements: int = MAX_REFINEMENTS


@dataclass
class AttemptState:
    """Mutable bookkeeping for one attempt."""

    attempt_id: str
    strategy: str
    metrics: AttemptMetrics
    outcomes: List[Outcome] = field(default_factory=list)
    terminal_outcomes: List[Outcome] = field(default_factory=list)
    scout: Optional[ScoutResult] = None
    implementer: Optional[ImplementerResult] = None
    reviewer: Optional[ReviewerResult] = None
    raw: Dict[str, str] = field(default_factory=dict)
    refinements_used: int = 0
    test_evidence_text: str = ""
    unaddressed_defects: Tuple[str, ...] = ()

    def note(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)
        self.terminal_outcomes.append(outcome)
        self.metrics.outcomes_seen = tuple(self.outcomes)

    def begin_candidate(self) -> None:
        """A fresh candidate is about to be evaluated.

        Clears candidate-scoped failures from the terminal set while keeping the
        full history. Attempt-level problems (source mismatch, grounding,
        protocol, timeout) are unaffected: they are properties of the attempt,
        not of one candidate.
        """
        self.terminal_outcomes = [
            outcome
            for outcome in self.terminal_outcomes
            if outcome not in CANDIDATE_SCOPED_OUTCOMES
        ]

    def finish(self) -> Outcome:
        from .taxonomy import first_matching

        self.metrics.refinements_used = self.refinements_used
        self.metrics.refinement_budget = MAX_REFINEMENTS
        self.metrics.review.unaddressed_after_refinement = self.unaddressed_defects
        outcome = first_matching(self.terminal_outcomes)
        self.metrics.outcome = outcome
        self.metrics.outcomes_seen = tuple(self.outcomes)
        return outcome


@dataclass
class EvaluationOutcome:
    """Result of applying one candidate patch and running acceptance."""

    patch_text: str
    applied: bool
    apply_reason: str
    applier: str
    changed_files: Tuple[str, ...]
    syntax_ok: Optional[bool]
    syntax_errors: Dict[str, str]
    tests: TestMetric
    patch_present: bool = True
    broader_skipped: bool = False
    apply_detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskRunResult:
    task: RealTask
    task_metrics: TaskMetrics
    attempts: List[AttemptState]
    run_dir: Optional[Any] = None
    authoritative_source_unchanged: bool = True

    def attempt(self, attempt_id: str) -> Optional[AttemptState]:
        for state in self.attempts:
            if state.attempt_id == attempt_id:
                return state
        return None


class BenchmarkRunner:
    """Runs strategies against frozen fixtures and writes evidence."""

    def __init__(
        self,
        adapter: ChatAdapter,
        run_dir,
        options: Optional[RunnerOptions] = None,
        *,
        work_root: Optional[Path] = None,
        harness_root: Optional[Path] = None,
        source_root: Optional[Path] = None,
    ):
        self.adapter = adapter
        self.run_dir = run_dir
        self.options = (options or RunnerOptions()).clamp()
        self.work_root = Path(work_root) if work_root else Path(run_dir.path) / "work"
        self.harness_root = Path(harness_root) if harness_root else Path(__file__).resolve().parent.parent
        self.source_root = Path(source_root) if source_root else None
        self._worktrees: List[EvaluationWorktree] = []

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _overhead_start(self) -> float:
        return time.monotonic()

    def _record_overhead(self, state: AttemptState, started: float) -> None:
        state.metrics.harness_overhead_s += time.monotonic() - started

    def _call(self, state: AttemptState, role: Role, system: str, user: str):
        """One bounded model call. Returns ``(response, outcome_or_None)``."""
        try:
            response = self.adapter.complete(
                ChatRequest(role=role.value, system=system, user=user)
            )
        except AdapterError as exc:
            state.note(exc.outcome)
            state.metrics.notes.append("{} call failed: {}".format(role.value, exc))
            return None, exc.outcome

        state.metrics.calls.append(CallMetric.from_response(response, role))
        state.raw[role.value] = response.content

        if not response.content.strip():
            state.note(Outcome.EMPTY_OUTPUT)
            return response, Outcome.EMPTY_OUTPUT
        if response.finish_reason == "length":
            state.note(Outcome.TRUNCATED)
        return response, None

    def load_sources(self, task: RealTask, binding: SourceBinding) -> Tuple[Dict[str, str], Tuple[str, ...]]:
        """Bounded verbatim source text for the prompt, plus any truncation."""
        contents: Dict[str, str] = {}
        truncated: List[str] = []
        budget = self.options.max_source_bytes
        used = 0
        for path in task.prompt_files():
            target = Path(binding.source_root) / path
            if not target.is_file():
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
            if used + len(text) > budget:
                truncated.append(path)
                continue
            contents[path] = text
            used += len(text)
        return contents, tuple(truncated)

    def acceptance_lines(self, task: RealTask) -> Tuple[str, ...]:
        lines = []
        for command in task.acceptance.targeted:
            lines.append(" ".join(command))
        for command in task.acceptance.broader:
            lines.append("BROADER: " + " ".join(command))
        return tuple(lines)

    # ------------------------------------------------------------------
    # evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self, task: RealTask, patch_text: str, answer_text: str = ""
    ) -> EvaluationOutcome:
        started = self._overhead_start()
        guard_paths = [self.harness_root, task.source_dir, task.tests_dir]
        worktree = EvaluationWorktree.create(
            task,
            self.work_root,
            extra_allowed_programs=task.acceptance.allow_programs,
            guard_paths=guard_paths,
        )
        self._worktrees.append(worktree)
        before = worktree.hashes()
        if patch_text.strip():
            apply = worktree.apply_patch(patch_text)
            patch_present = True
        else:
            # Analysis deliverables carry no candidate patch. The worktree still
            # exists so that fixture-provided answer checks can run against the
            # exact bound source.
            from .patch import ApplyResult

            apply = ApplyResult(
                ok=True,
                applier="none",
                reason="no candidate patch (analysis deliverable)",
                before_hashes=before,
                after_hashes=before,
            )
            patch_present = False
        tests = TestMetric()
        syntax_ok: Optional[bool] = None
        syntax_errors: Dict[str, str] = {}
        changed = apply.changed_files
        if apply.ok:
            syntax_ok, syntax_errors = worktree.syntax(changed)
            worktree.write_answer(answer_text)
            for command in task.acceptance.targeted:
                tests.targeted.append(
                    worktree.run_command(command, self.options.test_timeout_s)
                )
            if tests.targeted_all_passed:
                for command in task.acceptance.broader:
                    tests.broader.append(
                        worktree.run_command(command, self.options.test_timeout_s)
                    )
        self._last_overhead = time.monotonic() - started
        return EvaluationOutcome(
            patch_text=patch_text,
            patch_present=patch_present,
            applied=apply.ok,
            apply_reason=apply.reason,
            applier=apply.applier,
            changed_files=changed,
            syntax_ok=syntax_ok,
            syntax_errors=syntax_errors,
            tests=tests,
            broader_skipped=bool(task.acceptance.broader) and not tests.broader,
            apply_detail=apply.to_dict(),
        )

    def _charge_overhead(self, state: AttemptState, started: float) -> None:
        self._record_overhead(state, started)

    # ------------------------------------------------------------------
    # candidate patch handling
    # ------------------------------------------------------------------

    def _patch_from_result(self, result: ImplementerResult, state: AttemptState) -> str:
        extracted = extract_patch(result.patch)
        patch_metric = state.metrics.patch
        patch_metric.produced = bool(result.patch.strip())
        patch_metric.extracted = not extracted.is_empty
        patch_metric.extraction_strategy = extracted.strategy
        return extracted.text

    def _record_evaluation(
        self,
        state: AttemptState,
        task: RealTask,
        evaluation: EvaluationOutcome,
        declared_files: Sequence[str],
    ) -> None:
        state.begin_candidate()
        patch_metric = state.metrics.patch
        patch_metric.safety_ok = bool(evaluation.apply_detail.get("safety_ok", True))
        patch_metric.safety_reason = str(evaluation.apply_detail.get("safety_reason") or "")
        patch_metric.applied = evaluation.applied
        patch_metric.applier = evaluation.applier
        patch_metric.apply_reason = evaluation.apply_reason
        patch_metric.files_changed = tuple(evaluation.changed_files)
        patch_metric.files_declared_to_change = tuple(declared_files)
        allowed = set(task.source.paths)
        patch_metric.unnecessary_changed_files = tuple(
            path
            for path in evaluation.changed_files
            if path not in set(declared_files) and path not in allowed
        )
        patch_metric.syntax_ok = evaluation.syntax_ok
        patch_metric.syntax_errors = dict(evaluation.syntax_errors)
        state.metrics.tests = evaluation.tests

        if not evaluation.applied:
            hint = evaluation.apply_detail.get("outcome_hint")
            state.note(Outcome(hint) if hint in {o.value for o in Outcome} else Outcome.PATCH_DOES_NOT_APPLY)
            return
        if evaluation.syntax_ok is False:
            state.note(Outcome.TARGETED_TEST_FAILURE)
            return
        if not evaluation.tests.targeted_all_passed:
            state.note(Outcome.TARGETED_TEST_FAILURE)
            return
        if evaluation.tests.broader and not evaluation.tests.broader_all_passed:
            state.note(Outcome.REGRESSION_FAILURE)

    def test_evidence_text(self, evaluation: EvaluationOutcome) -> str:
        lines: List[str] = []
        lines.append("candidate patch applied: {}".format(evaluation.applied))
        lines.append("applier: {}".format(evaluation.applier or "(none)"))
        lines.append("changed files: {}".format(", ".join(evaluation.changed_files) or "(none)"))
        if evaluation.syntax_ok is not None:
            lines.append("syntax ok: {}".format(evaluation.syntax_ok))
            for path, message in sorted(evaluation.syntax_errors.items()):
                lines.append("  syntax error {}: {}".format(path, message))
        lines.append("")
        lines.append("TARGETED ACCEPTANCE")
        for command in evaluation.tests.targeted:
            lines.append(
                "  $ {}".format(" ".join(command.argv))
            )
            lines.append(
                "    returncode={} timed_out={} passed={} duration_s={:.3f}".format(
                    command.returncode, command.timed_out, command.passed, command.duration_s
                )
            )
            if command.stdout.strip():
                lines.append("    stdout: " + command.stdout.strip()[-2000:].replace("\n", "\n    "))
            if command.stderr.strip():
                lines.append("    stderr: " + command.stderr.strip()[-2000:].replace("\n", "\n    "))
        if evaluation.tests.broader:
            lines.append("")
            lines.append("BROADER ACCEPTANCE")
            for command in evaluation.tests.broader:
                lines.append("  $ {}".format(" ".join(command.argv)))
                lines.append(
                    "    returncode={} timed_out={} passed={} duration_s={:.3f}".format(
                        command.returncode, command.timed_out, command.passed, command.duration_s
                    )
                )
                if command.stdout.strip():
                    lines.append(
                        "    stdout: " + command.stdout.strip()[-2000:].replace("\n", "\n    ")
                    )
        elif evaluation.broader_skipped:
            lines.append("")
            lines.append("BROADER ACCEPTANCE: not run (targeted acceptance failed)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # strategies
    # ------------------------------------------------------------------

    def run_single(self, task: RealTask, binding: SourceBinding) -> AttemptState:
        if task.deliverable == "analysis":
            return self._run_single_analysis(task, binding)
        state = self._new_state(task, "single")
        contents, truncated = self.load_sources(task, binding)
        sources = role_contracts.source_block(task.prompt_files(), contents, truncated)
        prompt = role_contracts.single_prompt(
            task.problem,
            task.constraints,
            self.acceptance_lines(task),
            sources,
            task.task_id,
            task.task_family,
        )

        started = self._overhead_start()
        response, failure = self._call(state, Role.SINGLE, "You are a software engineer.", prompt)
        self._charge_overhead(state, started)
        if response is None or failure is not None:
            return state

        try:
            result = ImplementerResult.parse(response.content)
        except RoleProtocolError as exc:
            state.note(Outcome(exc.outcome_hint))
            state.metrics.notes.append("single role parse failed: {}".format(exc))
            return state
        state.implementer = result
        state.metrics.grounding = grounding_from_text(
            response.content,
            task.source.paths,
            task.relevant_source_files,
            task.acceptance.required_files_mentioned,
            task.acceptance.required_answer_contains,
        )
        if grounding_failure(state.metrics.grounding):
            state.note(Outcome.GROUNDING_FAILURE)

        patch_text = self._patch_from_result(result, state)
        if not patch_text:
            state.note(Outcome.INVALID_PATCH)
            state.metrics.notes.append("no unified diff could be extracted from the reply")
            return state

        started = self._overhead_start()
        evaluation = self.evaluate(task, patch_text, response.content)
        self._charge_overhead(state, started)
        self._record_evaluation(state, task, evaluation, declared_files=task.relevant_source_files)
        state.test_evidence_text = self.test_evidence_text(evaluation)
        state.metrics.notes.extend(self._context_notes(task, truncated))
        return state

    def run_scout(self, task: RealTask, binding: SourceBinding) -> AttemptState:
        state = self._new_state(task, "scout")
        contents, truncated = self.load_sources(task, binding)
        sources = role_contracts.source_block(task.prompt_files(), contents, truncated)
        prompt = role_contracts.scout_prompt(
            task.problem,
            task.constraints,
            self.acceptance_lines(task),
            sources,
            task.relevant_source_files,
        )
        started = self._overhead_start()
        response, failure = self._call(state, Role.SCOUT, "You are a staff engineer debugging.", prompt)
        self._charge_overhead(state, started)
        if response is None or failure is not None:
            return state
        try:
            scout = ScoutResult.parse(response.content)
        except RoleProtocolError as exc:
            state.note(Outcome(exc.outcome_hint))
            state.metrics.notes.append("scout role parse failed: {}".format(exc))
            return state
        state.scout = scout
        state.metrics.grounding = grounding_from_text(
            response.content,
            task.source.paths,
            task.relevant_source_files,
            task.acceptance.required_files_mentioned,
        )
        if grounding_failure(state.metrics.grounding):
            state.note(Outcome.GROUNDING_FAILURE)
        return state

    def run_implement(
        self, task: RealTask, binding: SourceBinding, scout: Optional[ScoutResult] = None
    ) -> AttemptState:
        strategy = "implement"
        state = self._new_state(task, strategy)
        state.scout = scout
        contents, truncated = self.load_sources(task, binding)
        sources = role_contracts.source_block(task.prompt_files(), contents, truncated)
        prompt = role_contracts.implementer_prompt(
            task.problem, task.constraints, self.acceptance_lines(task), sources, scout
        )
        started = self._overhead_start()
        response, failure = self._call(state, Role.IMPLEMENTER, "You are a software engineer.", prompt)
        self._charge_overhead(state, started)
        if response is None or failure is not None:
            return state
        try:
            result = ImplementerResult.parse(response.content)
        except RoleProtocolError as exc:
            state.note(Outcome(exc.outcome_hint))
            state.metrics.notes.append("implementer role parse failed: {}".format(exc))
            return state
        state.implementer = result
        state.metrics.grounding = grounding_from_text(
            response.content,
            task.source.paths,
            task.relevant_source_files,
            task.acceptance.required_files_mentioned,
        )
        if grounding_failure(state.metrics.grounding):
            state.note(Outcome.GROUNDING_FAILURE)

        patch_text = self._patch_from_result(result, state)
        if not patch_text:
            state.note(Outcome.INVALID_PATCH)
            return state
        started = self._overhead_start()
        evaluation = self.evaluate(task, patch_text, response.content)
        self._charge_overhead(state, started)
        declared = scout.relevant_files if scout else task.relevant_source_files
        self._record_evaluation(state, task, evaluation, declared)
        state.test_evidence_text = self.test_evidence_text(evaluation)
        return state

    def run_review(
        self,
        task: RealTask,
        binding: SourceBinding,
        patch_text: str,
        candidate_answer: str = "",
        scout: Optional[ScoutResult] = None,
        refinement_round: int = 0,
        candidate_label: str = "CANDIDATE PATCH",
        allow_refinement: Optional[bool] = None,
    ) -> Tuple[AttemptState, Optional[EvaluationOutcome]]:
        """Review a candidate patch, optionally spending one refinement.

        Returns the attempt state (carrying the post-review decision and, when a
        refinement ran, the final validation evidence).
        """
        strategy = "review" if refinement_round == 0 else "swarm-refinement"
        state = self._new_state(task, strategy)
        state.scout = scout

        started = self._overhead_start()
        evaluation = self.evaluate(task, patch_text, candidate_answer)
        self._charge_overhead(state, started)
        declared = scout.relevant_files if scout else task.relevant_source_files
        self._record_evaluation(state, task, evaluation, declared)
        state.test_evidence_text = self.test_evidence_text(evaluation)

        verified = self.verify(task)
        if not verified.ok:
            state.note(Outcome.SOURCE_MISMATCH)
            state.metrics.notes.extend(verified.reasons)
            state.metrics.notes.append(
                "reviewer was not invoked: source binding drifted before the review call"
            )
            return state, evaluation

        prompt = role_contracts.reviewer_prompt(
            task.problem,
            task.constraints,
            binding_prompt_block(verified),
            patch_text or candidate_answer,
            state.test_evidence_text,
            candidate_label=candidate_label,
        )
        started = self._overhead_start()
        response, failure = self._call(state, Role.REVIEWER, "You are a demanding code reviewer.", prompt)
        self._charge_overhead(state, started)
        if response is None or failure is not None:
            return state, evaluation
        try:
            reviewer = ReviewerResult.parse(response.content)
        except RoleProtocolError as exc:
            state.note(Outcome(exc.outcome_hint))
            state.metrics.notes.append("reviewer role parse failed: {}".format(exc))
            return state, evaluation

        state.reviewer = reviewer
        state.metrics.review = ReviewMetric(
            ran=True,
            verdict=reviewer.verdict.value,
            confidence=reviewer.confidence,
            defects=summarise_defects(reviewer),
            missing_coverage=reviewer.missing_coverage,
            contract_violations=reviewer.contract_violations,
        )

        if reviewer.verdict.value == "accept":
            return state, evaluation

        refinement_enabled = (
            self.options.allow_refinement if allow_refinement is None else allow_refinement
        )
        if not refinement_enabled:
            state.note(Outcome.REVIEW_REJECTED)
            state.metrics.notes.append("reviewer requested changes but refinement is disabled")
            return state, evaluation

        state.metrics.notes.append("reviewer requested changes; one refinement round spent")
        self._request_refinement(state)
        contents, truncated = self.load_sources(task, verified)
        sources = role_contracts.source_block(task.prompt_files(), contents, truncated)
        prompt = role_contracts.refinement_prompt(
            task.problem,
            task.constraints,
            self.acceptance_lines(task),
            sources,
            scout,
            response.content,
            reviewer,
            patch_text,
        )
        started = self._overhead_start()
        refine_response, refine_failure = self._call(
            state, Role.IMPLEMENTER, "You are a software engineer revising your patch.", prompt
        )
        self._charge_overhead(state, started)
        if refine_response is None or refine_failure is not None:
            state.unaddressed_defects = tuple(
                d.description for d in reviewer.defects
            )
            return state, evaluation

        try:
            refined = ImplementerResult.parse(refine_response.content)
        except RoleProtocolError as exc:
            state.note(Outcome(exc.outcome_hint))
            state.unaddressed_defects = tuple(d.description for d in reviewer.defects)
            return state, evaluation

        refined_text = extract_patch(refined.patch)
        if refined_text.is_empty:
            state.note(Outcome.INVALID_PATCH)
            state.unaddressed_defects = tuple(d.description for d in reviewer.defects)
            return state, evaluation

        started = self._overhead_start()
        final = self.evaluate(task, refined_text.text, refine_response.content)
        self._charge_overhead(state, started)
        self._record_evaluation(state, task, final, declared)
        state.test_evidence_text = self.test_evidence_text(final)
        state.raw["patch_final"] = refined_text.text
        state.unaddressed_defects = self._unaddressed(reviewer, final)
        return state, final

    def _unaddressed(self, reviewer: ReviewerResult, final: EvaluationOutcome) -> Tuple[str, ...]:
        """Defects the reviewer raised that final validation still shows up."""
        if final.tests.targeted_all_passed and (not final.tests.broader or final.tests.broader_all_passed):
            return ()
        blob = "\n".join(
            (c.stdout or "") + "\n" + (c.stderr or "")
            for c in list(final.tests.targeted) + list(final.tests.broader)
            if not c.passed
        ).lower()
        return tuple(
            d.description for d in reviewer.defects if d.description and d.description[:40].lower() in blob
        )

    def _request_refinement(self, state: AttemptState) -> None:
        if state.refinements_used >= self.options.max_refinements:
            raise RefinementBudgetExhausted(
                "attempt {} already used its {} refinement budget".format(
                    state.attempt_id, state.refinements_used
                )
            )
        state.refinements_used += 1

    def _run_single_analysis(self, task: RealTask, binding: SourceBinding) -> AttemptState:
        """Single attempt on an analysis-only task: scout schema, no patch."""
        state = self._new_state(task, "single")
        contents, truncated = self.load_sources(task, binding)
        sources = role_contracts.source_block(task.prompt_files(), contents, truncated)
        prompt = role_contracts.single_analysis_prompt(
            task.problem,
            task.constraints,
            self.acceptance_lines(task),
            sources,
            task.task_id,
            task.task_family,
        )
        started = self._overhead_start()
        response, failure = self._call(state, Role.SINGLE, "You are a software engineer.", prompt)
        self._charge_overhead(state, started)
        if response is None or failure is not None:
            return state
        try:
            scout = ScoutResult.parse(response.content)
        except RoleProtocolError as exc:
            state.note(Outcome(exc.outcome_hint))
            state.metrics.notes.append("single analysis parse failed: {}".format(exc))
            return state
        state.scout = scout
        state.metrics.grounding = grounding_from_text(
            response.content,
            task.source.paths,
            task.relevant_source_files,
            task.acceptance.required_files_mentioned,
            task.acceptance.required_answer_contains,
        )
        if grounding_failure(state.metrics.grounding):
            state.note(Outcome.GROUNDING_FAILURE)

        started = self._overhead_start()
        evaluation = self.evaluate(task, "", response.content)
        self._charge_overhead(state, started)
        self._record_evaluation(state, task, evaluation, declared_files=())
        state.test_evidence_text = self.test_evidence_text(evaluation)
        state.metrics.notes.append("analysis deliverable: no candidate patch was produced")
        state.metrics.notes.extend(self._context_notes(task, truncated))
        return state

    def run_swarm(self, task: RealTask, binding: SourceBinding) -> AttemptState:
        if task.deliverable == "analysis":
            return self._run_swarm_analysis(task, binding)
        state = self._new_state(task, "swarm")
        contents, truncated = self.load_sources(task, binding)
        sources = role_contracts.source_block(task.prompt_files(), contents, truncated)
        scout_prompt_text = role_contracts.scout_prompt(
            task.problem,
            task.constraints,
            self.acceptance_lines(task),
            sources,
            task.relevant_source_files,
        )
        started = self._overhead_start()
        response, failure = self._call(
            state, Role.SCOUT, "You are a staff engineer debugging.", scout_prompt_text
        )
        self._charge_overhead(state, started)
        if response is None or failure is not None:
            return state

        try:
            scout = ScoutResult.parse(response.content)
        except RoleProtocolError as exc:
            state.note(Outcome(exc.outcome_hint))
            state.metrics.notes.append("scout role parse failed: {}".format(exc))
            return state
        state.scout = scout
        state.metrics.grounding = grounding_from_text(
            response.content,
            task.source.paths,
            task.relevant_source_files,
            task.acceptance.required_files_mentioned,
        )
        if grounding_failure(state.metrics.grounding):
            state.note(Outcome.GROUNDING_FAILURE)

        implement_prompt = role_contracts.implementer_prompt(
            task.problem, task.constraints, self.acceptance_lines(task), sources, scout
        )
        started = self._overhead_start()
        impl_response, impl_failure = self._call(
            state, Role.IMPLEMENTER, "You are a software engineer.", implement_prompt
        )
        self._charge_overhead(state, started)
        if impl_response is None or impl_failure is not None:
            return state

        try:
            implementer = ImplementerResult.parse(impl_response.content)
        except RoleProtocolError as exc:
            state.note(Outcome(exc.outcome_hint))
            state.metrics.notes.append("implementer role parse failed: {}".format(exc))
            return state
        state.implementer = implementer

        candidate = extract_patch(implementer.patch)
        state.metrics.patch.produced = bool(implementer.patch.strip())
        state.metrics.patch.extracted = not candidate.is_empty
        state.metrics.patch.extraction_strategy = candidate.strategy
        if candidate.is_empty:
            state.note(Outcome.INVALID_PATCH)
            state.metrics.notes.append("no unified diff could be extracted from the reply")
            return state

        review_state, final_evaluation = self.run_review(
            task,
            binding,
            candidate.text,
            candidate_answer=impl_response.content,
            scout=scout,
            refinement_round=0,
        )
        self._merge_review(state, review_state)
        state.metrics.notes.extend(self._context_notes(task, truncated))
        return state

    def _run_swarm_analysis(self, task: RealTask, binding: SourceBinding) -> AttemptState:
        """Swarm on an analysis-only task: scout, then independent review.

        There is no implementer and no refinement round here: a candidate
        analysis is not a patch, and the bounded refinement budget is defined in
        terms of patch revision. A ``revise`` verdict therefore terminates the
        attempt as ``REVIEW_REJECTED``.
        """
        state = self._new_state(task, "swarm")
        contents, truncated = self.load_sources(task, binding)
        sources = role_contracts.source_block(task.prompt_files(), contents, truncated)
        scout_prompt_text = role_contracts.scout_prompt(
            task.problem,
            task.constraints,
            self.acceptance_lines(task),
            sources,
            task.relevant_source_files,
        )
        started = self._overhead_start()
        response, failure = self._call(
            state, Role.SCOUT, "You are a staff engineer debugging.", scout_prompt_text
        )
        self._charge_overhead(state, started)
        if response is None or failure is not None:
            return state
        try:
            scout = ScoutResult.parse(response.content)
        except RoleProtocolError as exc:
            state.note(Outcome(exc.outcome_hint))
            state.metrics.notes.append("scout role parse failed: {}".format(exc))
            return state
        state.scout = scout
        state.metrics.grounding = grounding_from_text(
            response.content,
            task.source.paths,
            task.relevant_source_files,
            task.acceptance.required_files_mentioned,
        )
        if grounding_failure(state.metrics.grounding):
            state.note(Outcome.GROUNDING_FAILURE)

        review_state, _ = self.run_review(
            task,
            binding,
            "",
            candidate_answer=response.content,
            scout=scout,
            refinement_round=0,
            candidate_label="CANDIDATE ANALYSIS (verbatim; this is the exact text the harness received)",
            allow_refinement=False,
        )
        self._merge_review(state, review_state)
        state.raw["implementer"] = response.content
        state.metrics.notes.append(
            "analysis deliverable: scout output reviewed, no implementer stage"
        )
        state.metrics.notes.extend(self._context_notes(task, truncated))
        return state

    def _merge_review(self, swarm: AttemptState, review: AttemptState) -> None:
        swarm.reviewer = review.reviewer
        swarm.metrics.calls.extend(review.metrics.calls)
        swarm.metrics.review = review.metrics.review
        swarm.refinements_used += review.refinements_used
        swarm.unaddressed_defects = review.unaddressed_defects
        swarm.metrics.tests = review.metrics.tests
        swarm.metrics.patch.applied = review.metrics.patch.applied
        swarm.metrics.patch.applier = review.metrics.patch.applier
        swarm.metrics.patch.apply_reason = review.metrics.patch.apply_reason
        swarm.metrics.patch.files_changed = review.metrics.patch.files_changed
        swarm.metrics.patch.files_declared_to_change = review.metrics.patch.files_declared_to_change
        swarm.metrics.patch.unnecessary_changed_files = review.metrics.patch.unnecessary_changed_files
        swarm.metrics.patch.syntax_ok = review.metrics.patch.syntax_ok
        swarm.metrics.patch.syntax_errors = review.metrics.patch.syntax_errors
        swarm.terminal_outcomes = list(review.terminal_outcomes)
        swarm.outcomes.extend(review.outcomes)
        swarm.metrics.outcomes_seen = tuple(swarm.outcomes)
        for note in review.metrics.notes:
            if note not in swarm.metrics.notes:
                swarm.metrics.notes.append(note)
        for key, value in review.raw.items():
            swarm.raw.setdefault(key, value)
        swarm.test_evidence_text = review.test_evidence_text

    # ------------------------------------------------------------------
    # binding / lifecycle
    # ------------------------------------------------------------------

    def verify(self, task: RealTask) -> SourceBinding:
        return verify_source_binding(
            task, self.source_root, require_head=self.options.require_head
        )

    def _context_notes(self, task: RealTask, truncated: Sequence[str]) -> List[str]:
        notes = []
        if truncated:
            notes.append(
                "source context truncated for: {} (max_source_bytes={})".format(
                    ", ".join(truncated), self.options.max_source_bytes
                )
            )
        return notes

    def _new_state(self, task: RealTask, strategy: str) -> AttemptState:
        metrics = AttemptMetrics(
            attempt_id="{}::{}".format(task.task_id, strategy),
            strategy=strategy,
            outcome=Outcome.PROTOCOL_FAILURE,
        )
        return AttemptState(attempt_id=metrics.attempt_id, strategy=strategy, metrics=metrics)

    def close(self) -> None:
        for worktree in self._worktrees:
            worktree.close()
        self._worktrees = []

    # ------------------------------------------------------------------
    # task level
    # ------------------------------------------------------------------

    def run_task(
        self,
        task: RealTask,
        strategies: Sequence[str],
        *,
        single_attempts: int = 1,
        patch_override: Optional[str] = None,
        scout_override: Optional[ScoutResult] = None,
    ) -> TaskRunResult:
        """Verify binding, run each requested strategy, and emit evidence."""
        before = _tree_fingerprint(task)

        started = self._overhead_start()
        binding = self.verify(task)
        if not binding.ok:
            state = self._new_state(task, "single")
            state.note(Outcome.SOURCE_MISMATCH)
            state.metrics.notes.extend(binding.reasons)
            state.finish()
            self._write_attempt_evidence(task, state, binding)
            task_metrics = TaskMetrics(
                task_id=task.task_id,
                task_family=task.task_family,
                fixture_sha256=task.fixture_sha256(),
                manifest_sha256=task.manifest_sha256(),
                snapshot_sha256=task.snapshot_sha256(),
                source_binding_sha256=binding.source_binding_sha256,
                source_head=task.source.head,
                attempts=[state.metrics],
            )
            return TaskRunResult(task=task, task_metrics=task_metrics, attempts=[state], run_dir=self.run_dir)

        task_metrics = TaskMetrics(
            task_id=task.task_id,
            task_family=task.task_family,
            fixture_sha256=task.fixture_sha256(),
            manifest_sha256=task.manifest_sha256(),
            snapshot_sha256=task.snapshot_sha256(),
            source_binding_sha256=binding.source_binding_sha256,
            source_head=task.source.head,
        )

        states: List[AttemptState] = []
        for strategy in strategies:
            if strategy == "single":
                for _ in range(max(1, single_attempts)):
                    states.append(self.run_single(task, binding))
            elif strategy == "scout":
                states.append(self.run_scout(task, binding))
            elif strategy == "implement":
                states.append(self.run_implement(task, binding, scout_override))
            elif strategy == "review":
                if patch_override is None:
                    state = self._new_state(task, "review")
                    state.note(Outcome.PROTOCOL_FAILURE)
                    state.metrics.notes.append("review stage requires a candidate patch")
                    states.append(state)
                else:
                    state, _ = self.run_review(
                        task, binding, patch_override, "", scout_override
                    )
                    states.append(state)
            elif strategy == "swarm":
                states.append(self.run_swarm(task, binding))
            else:
                raise ValueError("unknown strategy {!r}".format(strategy))

        for state in states:
            state.finish()
            task_metrics.attempts.append(state.metrics)
            self._write_attempt_evidence(task, state, binding)

        self._write_task_artifacts(task, task_metrics, states, time.monotonic() - started)
        after = _tree_fingerprint(task)
        unchanged = before == after
        return TaskRunResult(
            task=task,
            task_metrics=task_metrics,
            attempts=states,
            run_dir=self.run_dir,
            authoritative_source_unchanged=unchanged,
        )

    # ------------------------------------------------------------------
    # evidence
    # ------------------------------------------------------------------

    #: Which evidence directory each attempt strategy writes into. Strategies are
    #: not directory names: `implement` and `review` are single-role stages whose
    #: payloads belong with those roles.
    STRATEGY_DIRECTORIES = {
        "single": "single",
        "scout": "scout",
        "implement": "implementer",
        "review": "reviewer",
        "swarm-refinement": "reviewer",
        "swarm": "swarm",
    }

    def _write_attempt_evidence(
        self, task: RealTask, state: AttemptState, binding: SourceBinding
    ) -> None:
        directory = self.STRATEGY_DIRECTORIES.get(state.strategy)
        if directory is None:
            raise ValueError("no evidence directory for strategy {!r}".format(state.strategy))
        self.run_dir.write_role_json(
            directory,
            "metrics.json",
            state.metrics.to_dict(),
        )
        for role, content in state.raw.items():
            if role == "patch_final":
                continue
            self.run_dir.write_role_text(directory, "{}.txt".format(role), content)
        parsed: Dict[str, Any] = {
            "schema": SCHEMA_ROLE_RESULT,
            "attempt_id": state.attempt_id,
            "strategy": state.strategy,
            "binding": binding.to_dict(),
            "outcome": state.metrics.outcome.value,
            "notes": list(state.metrics.notes),
        }
        if state.scout is not None:
            parsed["scout"] = state.scout.to_dict()
        if state.implementer is not None:
            impl = state.implementer.to_dict()
            patch_text = extract_patch(state.implementer.patch).text
            impl["patch"] = patch_text
            impl["patch_characters"] = len(patch_text)
            parsed["implementer"] = impl
        if state.reviewer is not None:
            parsed["reviewer"] = state.reviewer.to_dict()
        if state.raw.get("patch_final"):
            parsed["final_patch"] = state.raw["patch_final"]
        self.run_dir.write_role_json(directory, "result.json", parsed)
        if state.test_evidence_text:
            self.run_dir.write_role_text(directory, "test-evidence.txt", state.test_evidence_text)

    def _write_task_artifacts(
        self,
        task: RealTask,
        task_metrics: TaskMetrics,
        states: Sequence[AttemptState],
        elapsed_s: float,
    ) -> None:
        self.run_dir.write_json("task.json", task.to_dict())
        self.run_dir.write_json("metrics.json", task_metrics.to_dict())

        patch_payload: List[Dict[str, Any]] = []
        for state in states:
            if state.raw.get("patch_final"):
                patch_payload.append(
                    {"attempt_id": state.attempt_id, "stage": "final", "text": state.raw["patch_final"]}
                )
            if state.implementer is not None:
                text = extract_patch(state.implementer.patch).text
                if text:
                    patch_payload.append(
                        {"attempt_id": state.attempt_id, "stage": "candidate", "text": text}
                    )
        if patch_payload:
            body = "\n".join(
                "### {} [{}]\n{}".format(item["attempt_id"], item["stage"], item["text"])
                for item in patch_payload
            )
            self.run_dir.write_text("patch.diff", body + "\n")

        tests_payload = {
            "schema": SCHEMA_TEST_RESULTS,
            "task_id": task.task_id,
            "fixture_sha256": task.fixture_sha256(),
            "attempts": [
                {
                    "attempt_id": state.attempt_id,
                    "strategy": state.strategy,
                    "outcome": state.metrics.outcome.value,
                    "patch_applied": state.metrics.patch.applied,
                    "changed_files": list(state.metrics.patch.files_changed),
                    "syntax_ok": state.metrics.patch.syntax_ok,
                    "tests": state.metrics.tests.to_dict(),
                }
                for state in states
            ],
            "harness_elapsed_s": round(elapsed_s, 6),
        }
        self.run_dir.write_json("test-results.json", tests_payload)


def _tree_fingerprint(task: RealTask) -> Dict[str, str]:
    """Hash every file the fixture owns, including its acceptance tests."""
    import hashlib

    out: Dict[str, str] = {}
    for base in (task.source_dir, task.tests_dir):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(task.root).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out[rel] = digest
    return out
