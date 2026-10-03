"""Canonical runner tests: stages, taxonomy, bounded refinement, authority.

Every behaviour listed as a required invariant in the harness specification has
a test here or in the sibling binding/patch modules.
"""
from __future__ import annotations

import json
import unittest

from test_realtask_support import (
    AUTO_INGEST_BUG_FIX,
    REPO_ROOT,
    TASKS_ROOT,
    AUTO_INGEST_CONTRACT,
    AUTO_INGEST_REFACTOR,
    AUTO_INGEST_REVIEW,
    AUTO_INGEST_TEST_GEN,
    MALFORMED_PATCH,
    NON_APPLYING_PATCH,
    REFERENCE_REPAIR,
    HarnessTestCase,
    analysis_reply,
    empty_reply,
    new_file_patch,
    patch_reply,
    review_reply,
    scout_reply,
)

from realtask.adapter import AdapterError, ScriptedAdapter, ScriptedResponse
from realtask.binding import binding_prompt_block
from realtask.evaluation import TESTS_DIRNAME
from realtask.metrics import grounding_failure, grounding_from_text
from realtask.runner import (
    CANDIDATE_SCOPED_OUTCOMES,
    BenchmarkRunner,
    RefinementBudgetExhausted,
    RunnerOptions,
)
from realtask.taxonomy import OUTCOME_PRECEDENCE, Outcome, first_matching, is_failure
from realtask.version import MAX_REFINEMENTS

TIMEOUT_REPLY = ScriptedResponse(content="", raise_error=AdapterError("deadline", Outcome.TIMEOUT))


def blocking_defect():
    return [{"severity": "blocker", "location": "cli.py:71", "description": "planning after close"}]


class TaxonomyTests(unittest.TestCase):
    def test_exactly_the_eleven_specified_outcomes_exist(self):
        self.assertEqual(
            sorted(o.value for o in Outcome),
            sorted(
                [
                    "PROTOCOL_FAILURE", "GROUNDING_FAILURE", "EMPTY_OUTPUT", "TRUNCATED",
                    "TIMEOUT", "INVALID_PATCH", "PATCH_DOES_NOT_APPLY",
                    "TARGETED_TEST_FAILURE", "REGRESSION_FAILURE", "SOURCE_MISMATCH",
                    "REVIEW_REJECTED", "SUCCESS",
                ]
            ),
        )

    def test_precedence_is_deterministic(self):
        self.assertEqual(OUTCOME_PRECEDENCE[0], Outcome.SOURCE_MISMATCH)
        self.assertEqual(OUTCOME_PRECEDENCE[-1], Outcome.SUCCESS)
        # Order of the input must not change the reduction.
        forward = first_matching([Outcome.INVALID_PATCH, Outcome.TARGETED_TEST_FAILURE])
        reverse = first_matching([Outcome.TARGETED_TEST_FAILURE, Outcome.INVALID_PATCH])
        self.assertEqual(forward, reverse)

    def test_source_mismatch_outranks_everything(self):
        everything = [o for o in Outcome if o is not Outcome.SUCCESS]
        self.assertIs(first_matching(everything), Outcome.SOURCE_MISMATCH)

    def test_success_is_not_a_failure(self):
        self.assertFalse(is_failure(Outcome.SUCCESS))
        self.assertTrue(is_failure(Outcome.REVIEW_REJECTED))


class GroundingTests(unittest.TestCase):
    def test_overlap_and_hallucination_are_separate(self):
        grounding = grounding_from_text(
            "look at auto_ingest/shorts/cli.py and also auto_ingest/shorts/ghost.py",
            ["auto_ingest/shorts/cli.py"],
            ["auto_ingest/shorts/cli.py"],
        )
        self.assertEqual(grounding.expected_overlap, 1.0)
        self.assertIn("auto_ingest/shorts/ghost.py", grounding.hallucinated_files)
        self.assertEqual(grounding.expected_overlap - 1.0, 0.0)
        self.assertLess(grounding.score, 1.0, "hallucination must lower the scalar")

    def test_ungrounded_reply_fails_the_gate(self):
        grounding = grounding_from_text(
            "I would restructure the module.", ["auto_ingest/shorts/cli.py"],
            ["auto_ingest/shorts/cli.py"],
        )
        self.assertTrue(grounding_failure(grounding))

    def test_missing_required_file_fails_the_gate(self):
        satisfied = grounding_from_text(
            "see auto_ingest/shorts/cli.py and auto_ingest/shorts/planner.py",
            ["auto_ingest/shorts/cli.py", "auto_ingest/shorts/planner.py"],
            [], required_files=["auto_ingest/shorts/planner.py"],
        )
        self.assertTrue(satisfied.required_files_satisfied)
        self.assertFalse(grounding_failure(satisfied))

        unsatisfied = grounding_from_text(
            "see auto_ingest/shorts/cli.py", ["auto_ingest/shorts/cli.py"],
            [], required_files=["auto_ingest/shorts/planner.py"],
        )
        self.assertFalse(unsatisfied.required_files_satisfied)
        self.assertTrue(grounding_failure(unsatisfied))

    def test_wrong_answer_content_is_not_a_grounding_failure(self):
        """Wrong content is a correctness problem, reported by acceptance tests."""
        grounding = grounding_from_text(
            "see auto_ingest/shorts/cli.py", ["auto_ingest/shorts/cli.py"],
            ["auto_ingest/shorts/cli.py"], required_answer_contains=["driver is fine"],
        )
        self.assertTrue(grounding.required_answer_missing)
        self.assertFalse(grounding_failure(grounding))


class SingleStageTests(HarnessTestCase):
    def test_successful_tiny_fixture_is_success(self):
        task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.SUCCESS)
        self.assertTrue(state.metrics.task_success)
        self.assertEqual(state.metrics.model_calls, 1)
        self.assertEqual(state.metrics.patch.files_changed, ("auto_ingest/shorts/cli.py",))
        self.assertEqual(state.metrics.patch.unnecessary_changed_files, ())
        self.assertTrue(state.metrics.tests.targeted_all_passed)
        self.assertEqual(state.metrics.tests.targeted[0].returncode, 0)
        self.assertTrue(state.metrics.patch.syntax_ok)

    def test_invalid_patch_maps_to_invalid_patch(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX, [patch_reply(MALFORMED_PATCH)], ["single"]
        )
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.INVALID_PATCH)
        self.assertTrue(state.metrics.patch.produced)
        self.assertFalse(state.metrics.patch.applied)
        self.assertEqual(state.metrics.tests.targeted, [])

    def test_unapplicable_patch_maps_to_patch_does_not_apply(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX, [patch_reply(NON_APPLYING_PATCH)], ["single"]
        )
        self.assertOutcome(result.attempts[0], Outcome.PATCH_DOES_NOT_APPLY)

    def test_targeted_test_failure_maps_to_task_failure(self):
        """A cosmetic patch applies cleanly and still fails acceptance."""
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX, [patch_reply(cosmetic_patch())], ["single"]
        )
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.TARGETED_TEST_FAILURE)
        self.assertTrue(state.metrics.patch.applied)
        self.assertTrue(state.metrics.tests.targeted)
        self.assertFalse(state.metrics.tests.targeted_all_passed)
        self.assertFalse(state.metrics.task_success)

    def test_prose_only_reply_is_invalid_patch_not_protocol_failure(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [patch_reply("I would move the call. No diff provided.")],
            ["single"],
        )
        self.assertOutcome(result.attempts[0], Outcome.INVALID_PATCH)

    def test_schema_violation_is_protocol_failure(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX, [ScriptedResponse(content="I think the bug is in cli.py")], ["single"]
        )
        self.assertOutcome(result.attempts[0], Outcome.PROTOCOL_FAILURE)

    def test_truncated_output_is_truncated(self):
        truncated = ScriptedResponse(
            content='{"patch": "diff --git a/auto_ingest/shorts/cli.py b/auto_ing',
            finish_reason="length",
        )
        _task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [truncated], ["single"])
        outcomes = [o.value for o in result.attempts[0].metrics.outcomes_seen]
        self.assertIn("TRUNCATED", outcomes)

    def test_empty_output_is_empty_output(self):
        _task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [empty_reply()], ["single"])
        self.assertOutcome(result.attempts[0], Outcome.EMPTY_OUTPUT)

    def test_timeout_maps_to_timeout(self):
        _task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [TIMEOUT_REPLY], ["single"])
        self.assertOutcome(result.attempts[0], Outcome.TIMEOUT)

    def test_ungrounded_single_attempt_fails_the_grounding_gate(self):
        reply = ScriptedResponse(
            content=json.dumps(
                {
                    "patch": REFERENCE_REPAIR,
                    "tests": [],
                    "assumptions": [],
                    "confidence": 0.4,
                }
            )
        )
        # Strip the file name from the reply so nothing is grounded.
        reply.content = reply.content.replace("auto_ingest/shorts/", "")
        _task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [reply], ["single"])
        outcomes = [o.value for o in result.attempts[0].metrics.outcomes_seen]
        self.assertIn("GROUNDING_FAILURE", outcomes)


def cosmetic_patch(rel: str = "auto_ingest/shorts/cli.py", source_root=None) -> str:
    """A diff that applies cleanly and changes nothing observable.

    Built from the frozen snapshot itself so the context always matches: it
    appends a trailing comment to one existing line. Acceptance must still fail.
    """
    import difflib

    root = source_root
    if root is None:
        from test_realtask_support import TASKS_ROOT

        root = TASKS_ROOT / AUTO_INGEST_BUG_FIX / "source"
    original = (root / rel).read_text()
    lines = original.splitlines(keepends=True)
    index = next(
        i for i, line in enumerate(lines) if line.startswith("log = logging.getLogger")
    )
    changed = list(lines)
    changed[index] = changed[index].rstrip("\n") + "  # harness: cosmetic edit\n"
    body = "".join(
        difflib.unified_diff(
            lines, changed, fromfile="a/" + rel, tofile="b/" + rel, n=3
        )
    )
    return "diff --git a/{rel} b/{rel}\n".format(rel=rel) + body


class BroaderAcceptanceTests(HarnessTestCase):
    """Targeted acceptance is necessary but not sufficient.

    A repair that fixes the defect while quietly changing the rest of the module
    must be reported as a regression, not as success.
    """

    def test_canonical_repair_passes_targeted_and_broader(self):
        _task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.SUCCESS)
        self.assertTrue(state.metrics.tests.targeted_all_passed)
        self.assertTrue(state.metrics.tests.broader_all_passed)
        self.assertEqual(len(state.metrics.tests.broader), 1)

    def test_broader_is_skipped_when_targeted_fails(self):
        """Broader runs only after targeted passes, and the skip is recorded."""
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX, [patch_reply(cosmetic_patch())], ["single"]
        )
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.TARGETED_TEST_FAILURE)
        self.assertEqual(state.metrics.tests.broader, [])
        self.assertIn(
            "BROADER ACCEPTANCE: not run (targeted acceptance failed)",
            state.test_evidence_text,
        )

    def test_regression_after_a_correct_repair_is_regression_failure(self):
        from test_realtask_support import regression_on_repair

        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX, [patch_reply(regression_on_repair())], ["single"]
        )
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.REGRESSION_FAILURE)
        self.assertTrue(state.metrics.patch.applied)
        self.assertTrue(state.metrics.tests.targeted_all_passed,
                        "the defect really was fixed; the regression is elsewhere")
        self.assertFalse(state.metrics.tests.broader_all_passed)
        self.assertEqual(state.metrics.tests.broader_failed[0].returncode, 1)
        stdout = state.metrics.tests.broader_failed[0].stdout
        self.assertIn("test_defaults_dirs_have_their_documented_fallbacks", stdout)
        self.assertIn("test_plan_defaults_are_intact", stdout)

    def test_broader_evidence_is_written_to_the_run(self):
        self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        tests = json.loads((self.run_dir.path / "test-results.json").read_text())
        attempt = tests["attempts"][0]["tests"]
        self.assertEqual(attempt["broader_total"], 1)
        self.assertEqual(attempt["broader_passed"], 1)
        self.assertTrue(attempt["broader_all_passed"])
        self.assertIn("BROADER ACCEPTANCE", (self.run_dir.path / "single" / "test-evidence.txt").read_text())

    def test_metrics_expose_broader_separately_from_targeted(self):
        _task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        payload = result.attempts[0].metrics.tests.to_dict()
        for key in ("targeted_total", "targeted_passed", "targeted_all_passed",
                    "broader_total", "broader_passed", "broader_all_passed"):
            self.assertIn(key, payload, key)


class SwarmStageTests(HarnessTestCase):
    def test_swarm_calls_scout_implementer_reviewer_in_order(self):
        task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(), review_reply("accept")],
            ["swarm"],
        )
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.SUCCESS)
        self.assertEqual(
            [c.role.value for c in state.metrics.calls],
            ["scout", "implementer", "reviewer"],
        )

    def test_swarm_grounding_comes_from_the_scout(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(), review_reply("accept")],
            ["swarm"],
        )
        self.assertEqual(
            result.attempts[0].metrics.grounding.mentioned_in_source,
            ("auto_ingest/shorts/cli.py",),
        )

    def test_reviewer_receives_the_exact_candidate_patch(self):
        """Not a paraphrase and not a re-generation: the identical bytes."""
        adapter, result = self.swarm_capture()
        self.assertOutcome(result.attempts[0], Outcome.SUCCESS)

        captured = adapter.requests[2].user
        self.assertIn("CANDIDATE PATCH (verbatim", captured)
        self.assertIn("@@ -64,15 +64,16 @@ def _cmd_plan(args) -> int:", captured)
        self.assertIn("+        plan = planner.plan_shorts(", captured)
        candidate = captured.split("CANDIDATE PATCH (verbatim", 1)[1]
        self.assertNotIn(
            "\\n", candidate[:400],
            "escaped newlines would mean the reviewer is not seeing real patch text",
        )
        # The bytes the harness applied and the bytes the reviewer saw agree.
        applied = result.attempts[0].metrics.patch.files_changed
        self.assertEqual(applied, ("auto_ingest/shorts/cli.py",))

    def test_reviewer_receives_the_exact_source_binding(self):
        _adapter, _result = self.swarm_capture()
        captured = self._last_review_prompt
        self.assertIn("SOURCE_BINDING (verified by the harness", captured)
        self.assertIn("head_status:", captured)
        self.assertIn("source_binding_sha256:", captured)
        binding = self.task(AUTO_INGEST_BUG_FIX)
        self.assertIn(
            [f.sha256 for f in binding.source.files][0], captured,
            "the reviewer must see the real per-file sha256, not a summary",
        )

    _last_review_prompt = ""

    def test_reviewer_cannot_inspect_a_different_checkout(self):
        """If the binding drifts before review, the reviewer is never invoked."""
        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = self.runner([scout_reply(), patch_reply(), review_reply("accept")])

        original_verify = runner.verify
        calls = {"n": 0}
        target = task.source_dir / "auto_ingest" / "shorts" / "cli.py"
        pristine = target.read_bytes()

        def drifting_verify(_task):
            calls["n"] += 1
            if calls["n"] > 1:
                # The source changes underneath the attempt, before the review.
                target.write_bytes(pristine + b"\n# drift\n")
                self.addCleanup(target.write_bytes, pristine)
            return original_verify(_task)

        runner.verify = drifting_verify
        result = runner.run_task(task, ["swarm"])
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.SOURCE_MISMATCH)
        self.assertEqual(
            [c.role.value for c in state.metrics.calls], ["scout", "implementer"],
            "the reviewer must not run against an unverified binding",
        )
        self.assertIn(
            "reviewer was not invoked: source binding drifted before the review call",
            state.metrics.notes,
        )

    def test_reviewer_is_never_handed_an_unverified_path(self):
        _adapter, _result = self.swarm_capture()
        captured = self._last_review_prompt
        self.assertIn("source_root:", captured)
        self.assertIn("repository: scottjoyner/auto-ingest", captured)
        self.assertIn("binding_mode: snapshot", captured)

    # -- helper ----------------------------------------------------------

    def swarm_capture(self):
        """Run one swarm and return the adapter so prompts can be inspected."""
        from realtask.evidence import RunDirectory

        adapter = ScriptedAdapter([scout_reply(), patch_reply(), review_reply("accept")])
        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = BenchmarkRunner(
            adapter,
            RunDirectory(self.tmp / "capture", "cap"),
            RunnerOptions(test_timeout_s=180.0),
            work_root=self.tmp / "capwork",
            harness_root=REPO_ROOT,
        )
        try:
            result = runner.run_task(task, ["swarm"])
        finally:
            runner.close()
        self.assertEqual(len(adapter.requests), 3)
        type(self)._last_review_prompt = adapter.requests[2].user
        return adapter, result


class RefinementBudgetTests(HarnessTestCase):
    def test_exactly_one_refinement_is_permitted(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(NON_APPLYING_PATCH), review_reply("revise"), patch_reply()],
            ["swarm"],
        )
        state = result.attempts[0]
        self.assertEqual(state.metrics.refinements_used, 1)
        self.assertOutcome(state, Outcome.SUCCESS)
        self.assertEqual(state.metrics.model_calls, 4)

    def test_a_second_refinement_is_impossible(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [
                scout_reply(), patch_reply(NON_APPLYING_PATCH), review_reply("revise"),
                patch_reply(NON_APPLYING_PATCH),
            ],
            ["swarm"],
        )
        state = result.attempts[0]
        self.assertEqual(state.metrics.refinements_used, 1)
        self.assertEqual(
            len([c for c in state.metrics.calls if c.role.value == "implementer"]), 2
        )

    def test_refinement_budget_cannot_be_raised_above_the_ceiling(self):
        options = RunnerOptions()
        options.max_refinements = 99
        options.clamp()
        self.assertEqual(options.max_refinements, MAX_REFINEMENTS)

    def test_requesting_a_refinement_past_the_budget_raises(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = self.runner([])
        state = runner._new_state(task, "swarm")
        runner._request_refinement(state)
        with self.assertRaises(RefinementBudgetExhausted):
            runner._request_refinement(state)

    def test_candidate_scoped_outcomes_are_cleared_by_a_new_candidate(self):
        """A repaired refinement is judged on its own final validation."""
        self.assertIn(Outcome.INVALID_PATCH, CANDIDATE_SCOPED_OUTCOMES)
        self.assertIn(Outcome.TARGETED_TEST_FAILURE, CANDIDATE_SCOPED_OUTCOMES)
        self.assertNotIn(Outcome.SOURCE_MISMATCH, CANDIDATE_SCOPED_OUTCOMES)
        self.assertNotIn(Outcome.PROTOCOL_FAILURE, CANDIDATE_SCOPED_OUTCOMES)

    def test_refinement_evidence_is_kept_separately(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(NON_APPLYING_PATCH), review_reply("revise"), patch_reply()],
            ["swarm"],
        )
        outcome = [o.value for o in result.attempts[0].metrics.outcomes_seen]
        self.assertIn("PATCH_DOES_NOT_APPLY", outcome, "history must survive")
        self.assertEqual(result.attempts[0].metrics.outcome, Outcome.SUCCESS)


class ReviewRejectionTests(HarnessTestCase):
    def test_rejection_without_a_refinement_budget_is_review_rejected(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(), review_reply("revise")],
            ["swarm"],
            runner={"options": RunnerOptions(test_timeout_s=180.0, allow_refinement=False)},
        )
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.REVIEW_REJECTED)
        self.assertEqual(state.metrics.refinements_used, 0)
        self.assertEqual(state.metrics.review.verdict, "revise")

    def test_blocking_defects_are_counted_separately(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(), review_reply("revise", blocking_defect())],
            ["swarm"],
        )
        review = result.attempts[0].metrics.review
        self.assertEqual(review.verdict, "revise")
        self.assertEqual(len(review.defects), 1)
        self.assertEqual(len(review.blocking_defects), 1)

    def test_reviewer_verdict_is_not_a_task_verdict(self):
        """A revise verdict that is then repaired must not poison the attempt."""
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(NON_APPLYING_PATCH), review_reply("revise"), patch_reply()],
            ["swarm"],
        )
        review = result.attempts[0].metrics.review
        self.assertEqual(review.verdict, "revise")
        self.assertEqual(result.attempts[0].metrics.outcome, Outcome.SUCCESS)


class SmallRefactorTests(HarnessTestCase):
    """The small_refactor fixture must be satisfiable, not merely strict.

    An oracle that nothing can pass is not a benchmark, it is a wall. These tests
    apply a real behaviour-preserving refactor and require SUCCESS, then require
    the untouched snapshot to fail, so the fixture discriminates.
    """

    def test_reference_refactor_passes(self):
        from test_realtask_support import reference_refactor

        _task, result = self.run_stages(
            AUTO_INGEST_REFACTOR, [patch_reply(reference_refactor())], ["single"]
        )
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.SUCCESS)
        self.assertEqual(state.metrics.patch.files_changed, ("auto_ingest/shorts/cli.py",))
        self.assertTrue(state.metrics.patch.syntax_ok)

    def test_untouched_source_fails_the_refactor_checks(self):
        """A cosmetic patch leaves the repetition in place and must not pass."""
        _task, result = self.run_stages(
            AUTO_INGEST_REFACTOR, [patch_reply(cosmetic_patch())], ["single"]
        )
        outcomes = [o.value for o in result.attempts[0].metrics.outcomes_seen]
        self.assertIn("TARGETED_TEST_FAILURE", outcomes)
        stdout = "\n".join(c.stdout for c in result.attempts[0].metrics.tests.targeted)
        self.assertIn("test_driver_close_is_stated_in_one_place", stdout)

    def test_a_patch_that_fixes_the_bug_instead_of_refactoring_fails(self):
        """The behavioural constraint is executable, not just advisory.

        Applying the bug fix to this fixture leaves the repetition in place and
        does not introduce a helper, so the refactor oracle rejects it. A patch
        that repairs the ordering defect *and* hoists it into the driver's live
        scope is caught by the explicit ordering check.
        """
        _task, result = self.run_stages(AUTO_INGEST_REFACTOR, [patch_reply()], ["single"])
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.TARGETED_TEST_FAILURE)
        self.assertTrue(state.metrics.patch.applied)
        stdout = "\n".join(c.stdout for c in state.metrics.tests.targeted)
        self.assertIn("test_driver_close_is_stated_in_one_place", stdout)


class AnalysisDeliverableTests(HarnessTestCase):
    def test_contract_reasoning_single_runs_the_answer_check(self):
        _task, result = self.run_stages(AUTO_INGEST_CONTRACT, [analysis_reply()], ["single"])
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.SUCCESS)
        self.assertFalse(state.metrics.patch.produced)
        self.assertTrue(state.metrics.tests.targeted_all_passed)

    def test_wrong_analysis_fails_targeted_acceptance(self):
        bad = ScriptedResponse(
            content=json.dumps(
                {
                    "root_cause": "auto_ingest/shorts/cli.py looks fine to me.",
                    "relevant_files": ["auto_ingest/shorts/cli.py"],
                    "plan": ["ship it"],
                    "risks": [],
                    "confidence": 0.9,
                }
            )
        )
        _task, result = self.run_stages(AUTO_INGEST_CONTRACT, [bad], ["single"])
        self.assertOutcome(result.attempts[0], Outcome.TARGETED_TEST_FAILURE)

    def test_analysis_swarm_is_scout_then_review_only(self):
        _task, result = self.run_stages(
            AUTO_INGEST_REVIEW, [analysis_reply(), review_reply("accept")], ["swarm"]
        )
        state = result.attempts[0]
        self.assertEqual([c.role.value for c in state.metrics.calls], ["scout", "reviewer"])
        self.assertEqual(state.metrics.refinements_used, 0)

    def test_analysis_swarm_rejection_is_review_rejected(self):
        _task, result = self.run_stages(
            AUTO_INGEST_REVIEW, [analysis_reply(), review_reply("revise")], ["swarm"]
        )
        self.assertOutcome(result.attempts[0], Outcome.REVIEW_REJECTED)


class TestGenerationTests(HarnessTestCase):
    def candidate_body(self) -> str:
        from test_realtask_support import TASKS_ROOT

        return (
            TASKS_ROOT / AUTO_INGEST_BUG_FIX / "tests" / "test_plan_driver_lifetime.py"
        ).read_text()

    def test_a_discriminating_regression_test_passes(self):
        patch = new_file_patch("_realtask_tests/test_candidate_lifetime.py", self.candidate_body())
        _task, result = self.run_stages(AUTO_INGEST_TEST_GEN, [patch_reply(patch)], ["single"])
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.SUCCESS)
        self.assertEqual(
            state.metrics.patch.files_changed,
            ("_realtask_tests/test_candidate_lifetime.py",),
        )

    def test_a_patch_that_adds_no_test_fails(self):
        cosmetic = cosmetic_patch(
            "auto_ingest/shorts/cli.py",
            TASKS_ROOT / AUTO_INGEST_TEST_GEN / "source",
        )
        _task, result = self.run_stages(
            AUTO_INGEST_TEST_GEN, [patch_reply(cosmetic)], ["single"]
        )
        self.assertOutcome(result.attempts[0], Outcome.TARGETED_TEST_FAILURE)

    def test_a_test_that_passes_on_the_buggy_source_is_rejected(self):
        """The meta-oracle proves discrimination rather than trusting green."""
        useless = (
            "def test_cli_imports():\n"
            "    import auto_ingest.shorts.cli  # noqa: F401\n"
        )
        patch = new_file_patch("_realtask_tests/test_useless.py", useless)
        reply = patch_reply(patch)
        reply.content = reply.content.replace(
            "assumptions", "see auto_ingest/shorts/cli.py"
        )
        _task, result = self.run_stages(AUTO_INGEST_TEST_GEN, [reply], ["single"])
        outcomes = [o.value for o in result.attempts[0].metrics.outcomes_seen]
        self.assertIn("TARGETED_TEST_FAILURE", outcomes)
        stdout = result.attempts[0].metrics.tests.targeted[0].stdout
        # A test that never fails on the defect is caught by the "detects the
        # defect" leg; one that always fails is caught by the "still fails after
        # the canonical repair" leg. Either way the candidate is rejected.
        self.assertTrue(
            "does not detect the driver-lifetime defect" in stdout
            or "still fails after the canonical repair" in stdout,
            stdout[-3000:],
        )


class AuthorityTests(HarnessTestCase):
    """The harness must be incapable of mutating the authoritative source."""

    def test_runner_never_modifies_the_authoritative_fixture(self):
        for task_id, responses, stages in (
            (AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"]),
            (AUTO_INGEST_BUG_FIX, [scout_reply(), patch_reply(), review_reply("accept")], ["swarm"]),
            (AUTO_INGEST_CONTRACT, [analysis_reply()], ["single"]),
            (AUTO_INGEST_REFACTOR, [patch_reply()], ["single"]),
        ):
            with self.subTest(task=task_id, stages=stages):
                task = self.task(task_id)
                before = self.tree_fingerprint(task)
                self.run_stages(task_id, responses, stages)
                self.assertEqual(self.tree_fingerprint(task), before)
                self.assertTrue(
                    (self.run_dir.path / "metrics.json").is_file()
                    or (self.run_dir.path / "test-results.json").is_file()
                )

    def test_model_output_cannot_reach_outside_the_worktree(self):
        from realtask.evaluation import EvaluationWorktree
        from realtask.patch import screen_patch

        task = self.task(AUTO_INGEST_BUG_FIX)
        escape = (
            "diff --git a/../../../../tmp/pwned.py b/../../../../tmp/pwned.py\n"
            "--- a/../../../../tmp/pwned.py\n+++ b/../../../../tmp/pwned.py\n"
            "@@ -0,0 +1 @@\n+owned\n"
        )
        report = screen_patch(escape, task.source.paths, task.source.writable_prefixes)
        self.assertFalse(report.ok)
        self.assertTrue(report.outside_worktree)
        wt = EvaluationWorktree.create(task, self.tmp / "work")
        self.addCleanup(wt.close)
        result = wt.apply_patch(escape)
        self.assertFalse(result.ok)
        self.assertFalse((self.tmp.parent / "pwned.py").exists())

    def test_no_production_scheduler_is_imported(self):
        """Static guard: the harness couples to no scheduling subsystem.

        Prose is fine -- the docstrings say what the harness does *not* do. What
        must not exist is an import or attribute reference to one.
        """
        import pathlib
        import re

        coupling = re.compile(
            r"^\s*(?:from|import)\s+(assistx|auto_router|auto_ingest|auto_assist|"
            r"scheduler|dispatch)\b|\b(assistx|auto_router)\.[a-z_]+\s*\(",
            re.IGNORECASE | re.MULTILINE,
        )
        package = pathlib.Path(__file__).resolve().parent / "realtask"
        checked = 0
        for path in sorted(package.glob("*.py")):
            checked += 1
            text = path.read_text()
            match = coupling.search(text)
            if match:
                line = text[: match.start()].count("\n") + 1
                self.fail(
                    "{}:{} couples to a production subsystem: {!r}".format(
                        path.name, line, match.group(0).strip()
                    )
                )
        self.assertGreaterEqual(checked, 10)

    def test_harness_writes_only_under_its_own_output_root(self):
        """No absolute write paths are hardcoded anywhere in the package."""
        import pathlib
        import re

        writers = re.compile(r"""\bopen\(\s*['"]/(?!tmp|dev)""")
        package = pathlib.Path(__file__).resolve().parent / "realtask"
        for path in sorted(package.glob("*.py")):
            self.assertIsNone(
                writers.search(path.read_text()),
                "{} writes to a hardcoded absolute path".format(path.name),
            )

    def test_harness_has_no_write_access_to_the_source_snapshot(self):
        """Sanity: the fixture snapshot is byte-identical after sealing checks."""
        from test_realtask_support import TASKS_ROOT
        import seal_realtask_fixture

        self.assertEqual(
            seal_realtask_fixture.main([str(TASKS_ROOT / AUTO_INGEST_BUG_FIX), "--check"]), 0
        )


if __name__ == "__main__":
    unittest.main()
