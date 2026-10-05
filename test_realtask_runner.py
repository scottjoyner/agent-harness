"""Canonical runner tests: stages, taxonomy, bounded refinement, authority.

Every behaviour listed as a required invariant in the harness specification has
a test here or in the sibling binding/patch modules.
"""
from __future__ import annotations

import shutil
from pathlib import Path
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
from realtask.fixtures import load_task
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
    def test_exactly_the_specified_outcomes_exist(self):
        """The taxonomy is a published contract, so it is enumerated, not derived.

        ``TOOL_CALL_REQUESTED`` was added after the first live run: a tool-tuned
        model answered with a tool call, which was being filed as TRUNCATED --
        true, but it pointed an operator at the token budget instead of at the
        role contract that already says no tools exist.
        """
        self.assertEqual(
            sorted(o.value for o in Outcome),
            sorted(
                [
                    "PROTOCOL_FAILURE", "GROUNDING_FAILURE", "EMPTY_OUTPUT", "TRUNCATED",
                    "TIMEOUT", "INVALID_PATCH", "PATCH_DOES_NOT_APPLY",
                    "TARGETED_TEST_FAILURE", "REGRESSION_FAILURE", "SOURCE_MISMATCH",
                    "REVIEW_REJECTED", "SUCCESS", "TOOL_CALL_REQUESTED",
                ]
            ),
        )

    def test_a_tool_call_reply_is_classified_not_mistaken_for_truncation(self):
        from realtask.roles import requested_tools

        self.assertEqual(
            requested_tools('<tool_call name="read_file" call_id="abc">'),
            ("read_file",),
        )
        self.assertEqual(requested_tools("I have no tools to offer."), ())
        self.assertEqual(
            requested_tools('{"root_cause": "no tool call here"}'), ()
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


class OverheadAccountingTests(HarnessTestCase):
    """Harness overhead must not include the model call it wraps.

    Found by the first live run against a real endpoint, not by any test: the
    scripted adapter returns instantly, so model wall time is ~0ms and
    double-counting it is invisible. Against a real 3B model that took 54
    seconds, ``harness_overhead_s`` came out at 53.95 -- the same figure as
    ``model_wall_s`` -- and ``total_wall_s`` was exactly twice ``model_wall_s``.

    That corrupts the number the comparison artifact exists to report:
    ``controller_integration_overhead.harness_owned_seconds`` and the cost
    difference between single and swarm. A harness cannot measure whether role
    separation is worth its overhead while billing the model's own latency to
    the overhead.

    The test is a *difference*, not a threshold. Running the acceptance suite
    costs about two seconds of genuine harness work whatever the model does, so
    "overhead is small" is not the property. The property is that overhead does
    not move when the model gets slower -- which is exactly what double-counting
    would change.
    """

    def run_with_latency(self, latency, responses, stages, task_id):
        """One attempt whose model calls each take ``latency`` seconds.

        The adapter must *report* the latency, not merely spend it: ``model_wall_s``
        sums ``CallMetric.wall_s``, which comes from the adapter's own
        ``ChatResponse``. ``ScriptedAdapter`` reports a constant 1.0s per call, so
        a scripted run has never had a realistic model wall time -- which is why
        double-counting it went unnoticed for the whole life of the harness.
        """
        import dataclasses
        import time as _time

        from realtask.adapter import ChatAdapter, ScriptedAdapter
        from realtask.runner import BenchmarkRunner, RunnerOptions

        inner = ScriptedAdapter(responses)

        class _Slow(ChatAdapter):
            identity = dict(inner.identity, adapter="scripted-slow")

            def complete(self, request):
                _time.sleep(latency)
                response = inner.complete(request)
                return dataclasses.replace(response, wall_s=latency)

        runner = BenchmarkRunner(
            _Slow(),
            self.run_dir,
            RunnerOptions(test_timeout_s=180.0),
            work_root=self.tmp / "work",
            harness_root=REPO_ROOT,
        )
        self.addCleanup(runner.close)
        return runner.run_task(self.task(task_id), stages).attempts[0].metrics

    #: Cached (added_model, added_overhead) from one fast and one slow run.
    _differential = None

    def overhead_differential(self):
        """How much overhead the harness reports when the model gets slower.

        The invariant is a *difference*, not a threshold: whatever genuine
        harness work costs on this machine -- running the campaign oracle is
        seconds of pytest, and more when the box is busy -- it should cost the
        same whether or not the model was slow. Only the delta can be asserted
        tightly.

        The earlier version of these tests compared ``harness_overhead_s``
        against a fixed 3.0s of simulated model latency. That passed on an idle
        machine and failed under load, because real harness work had grown past
        3s. A test whose verdict depends on how busy the host is is the same
        defect ``UndeclaredDependencyTests`` was written to prevent, so it is
        fixed here rather than papered over with a bigger constant.
        """
        from test_realtask_support import AUTO_INGEST_BUG_FIX, patch_reply

        if OverheadAccountingTests._differential is None:
            fast = self.run_with_latency(
                0.2, [patch_reply()], ["single"], AUTO_INGEST_BUG_FIX
            )
            slow = self.run_with_latency(
                5.0, [patch_reply()], ["single"], AUTO_INGEST_BUG_FIX
            )
            OverheadAccountingTests._differential = (
                slow.model_wall_s - fast.model_wall_s,
                slow.harness_overhead_s - fast.harness_overhead_s,
            )
        return OverheadAccountingTests._differential

    def test_a_slow_model_is_not_billed_to_the_harness(self):
        added_model, added_overhead = self.overhead_differential()
        self.assertGreater(
            added_model, 3.0,
            "the slow run was not actually slower by {}s; the fixture cannot "
            "be measuring what it claims".format(added_model),
        )
        self.assertLess(
            added_overhead, 0.5,
            "adding {:.1f}s of model time added {:.1f}s of harness overhead; "
            "the window around _call is billing model time to the harness, "
            "exactly as the first live run showed".format(
                added_model, added_overhead
            ),
        )

    def test_total_wall_does_not_double_count_the_model(self):
        from test_realtask_support import AUTO_INGEST_BUG_FIX, patch_reply

        metrics = self.run_with_latency(
            3.0, [patch_reply()], ["single"], AUTO_INGEST_BUG_FIX
        )
        self.assertAlmostEqual(
            metrics.total_wall_s,
            metrics.model_wall_s + metrics.harness_overhead_s,
            places=3,
        )
        # With the model time removed from overhead, total is model + genuine
        # harness work. Before the fix it was very close to 2x the model time,
        # so the leftover tracks model latency. Stated as a difference rather
        # than a ratio: see overhead_differential for why an absolute threshold
        # here would depend on how loaded the host is.
        _added_model, added_overhead = self.overhead_differential()
        self.assertLess(
            added_overhead, 0.5,
            "total_wall_s minus model_wall_s grows with model latency, so the "
            "model's time is being counted twice",
        )

    def test_a_swarm_charges_each_role_call_once(self):
        from test_realtask_support import (
            AUTO_INGEST_BUG_FIX, patch_reply, review_reply, scout_reply,
        )

        metrics = self.run_with_latency(
            2.0,
            [scout_reply(), patch_reply(), review_reply("accept")],
            ["swarm"],
            AUTO_INGEST_BUG_FIX,
        )
        self.assertEqual(
            [c.role.value for c in metrics.calls],
            ["scout", "implementer", "reviewer"],
            "the swarm did not make all three role calls",
        )
        self.assertGreaterEqual(metrics.model_wall_s, 5.5)
        self.assertLess(
            metrics.harness_overhead_s, metrics.model_wall_s,
            "a swarm's harness overhead {} exceeds its {}s of model time; "
            "every role call is being billed twice".format(
                metrics.harness_overhead_s, metrics.model_wall_s
            ),
        )


class BroaderAcceptanceTests(HarnessTestCase):
    """Targeted acceptance is necessary but not sufficient.

    A repair that fixes the defect while quietly changing the rest of the module
    must be reported as a regression, not as success.
    """

    def test_canonical_repair_passes_targeted_and_broader(self):
        _task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.SUCCESS)
        self.assertTestsPassed(state, "targeted")
        self.assertTestsPassed(state, "broader")
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
        self.assertEqual(attempt["broader_passed"], 1, attempt)
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


class AcceptanceTimeoutTests(HarnessTestCase):
    """A hung acceptance command is TIMEOUT, not a test failure."""

    def slow_fixture(self):
        import json as _json
        import shutil as _shutil

        root = self.tmp / "slow-fixture"
        if root.exists():
            _shutil.rmtree(root)
        _shutil.copytree(TASKS_ROOT / AUTO_INGEST_BUG_FIX, root)
        payload = _json.loads((root / "task.json").read_text())
        payload["acceptance"]["targeted"] = [[
            "${PYTHON}", "-c",
            "import time; time.sleep(30)",
        ]]
        payload["acceptance"]["broader"] = []
        (root / "task.json").write_text(_json.dumps(payload, indent=2, sort_keys=True))
        from test_realtask_support import seal_quietly

        seal_quietly(root)
        return load_task(root / "task.json")

    def test_hung_acceptance_command_is_timeout(self):
        task = self.slow_fixture()
        runner = self.runner(
            [patch_reply()], options=RunnerOptions(test_timeout_s=1.0)
        )
        result = runner.run_task(task, ["single"])
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.TIMEOUT)
        command = state.metrics.tests.targeted[0]
        self.assertTrue(command.timed_out)
        self.assertFalse(command.passed)
        self.assertTrue(
            any("test timeout" in note for note in state.metrics.notes),
            list(state.metrics.notes),
        )

    def test_timeout_is_reported_in_the_command_evidence(self):
        task = self.slow_fixture()
        runner = self.runner([patch_reply()], options=RunnerOptions(test_timeout_s=1.0))
        result = runner.run_task(task, ["single"])
        self.assertIn("timed_out=True", result.attempts[0].test_evidence_text)


class HarnessFailureTests(HarnessTestCase):
    """Always-emit: an unexpected harness failure must not erase the run."""

    def test_unexpected_error_in_an_attempt_is_recorded_not_raised(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        # One reply for the single attempt, then a full scout/implement/review
        # sequence so the swarm actually reaches evaluation before it blows up.
        runner = self.runner(
            [patch_reply(), scout_reply(), patch_reply(), review_reply("accept")]
        )

        def explode(*args, **kwargs):
            raise RuntimeError("simulated harness failure")

        runner.evaluate = explode
        result = runner.run_task(task, ["single", "swarm"])
        self.assertEqual(len(result.attempts), 2)
        for state in result.attempts:
            self.assertOutcome(state, Outcome.PROTOCOL_FAILURE)
            self.assertIn("RuntimeError: simulated harness failure", state.metrics.harness_error)
            self.assertTrue(
                any("evidence retained" in note for note in state.metrics.notes),
                list(state.metrics.notes),
            )
        self.assertEqual(
            result.attempts[0].metrics.harness_error,
            result.attempts[1].metrics.harness_error,
            "both attempts failed for the same reason, as expected here",
        )
        self.assertNotEqual(
            result.attempts[0].metrics.attempt_id,
            result.attempts[1].metrics.attempt_id,
            "attempt ids must stay unique across strategies",
        )

    def test_a_failing_attempt_does_not_stop_later_ones(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = self.runner([patch_reply(), patch_reply()])
        calls = {"n": 0}
        real = runner.evaluate

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient harness failure")
            return real(*args, **kwargs)

        runner.evaluate = flaky
        result = runner.run_task(task, ["single"], single_attempts=2)
        first, second = result.attempts
        self.assertIsNotNone(first.metrics.harness_error)
        self.assertIsNone(second.metrics.harness_error)
        self.assertOutcome(second, Outcome.SUCCESS)

    def test_evidence_is_still_written_after_a_harness_error(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = self.runner([patch_reply()])
        runner.evaluate = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        result = runner.run_task(task, ["single"])

        self.assertTrue((self.run_dir.path / "metrics.json").is_file())
        self.assertTrue((self.run_dir.path / "single" / "metrics.json").is_file())
        self.assertTrue((self.run_dir.path / "single" / "result.json").is_file())
        metrics = json.loads((self.run_dir.path / "metrics.json").read_text())
        self.assertIn("boom", metrics["attempts"][0]["harness_error"])
        self.assertEqual(metrics["attempts"][0]["outcome"], "PROTOCOL_FAILURE")

    def test_a_lost_artifact_write_is_recorded_not_swallowed(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = self.runner([patch_reply()])
        runner._write_task_artifacts = lambda *a, **k: (_ for _ in ()).throw(
            OSError("disk full")
        )
        result = runner.run_task(task, ["single"])
        self.assertIn("disk full", result.attempts[0].metrics.harness_error)
        failure = json.loads(
            (self.run_dir.path / "artifact-write-failure.json").read_text()
        )
        self.assertIn("disk full", failure["error"])


class SourceContextTests(HarnessTestCase):
    """Bounded prompt context must be bounded honestly."""

    def test_truncation_is_recorded_when_context_exceeds_the_budget(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = self.runner(
            [patch_reply()], options=RunnerOptions(max_source_bytes=1024)
        )
        result = runner.run_task(task, ["single"])
        notes = result.attempts[0].metrics.notes
        self.assertTrue(
            any("source context truncated" in note for note in notes), list(notes)
        )
        self.assertTrue(any("max_source_bytes=1024" in note for note in notes))

    def test_truncated_context_is_marked_in_the_prompt(self):
        from realtask.adapter import ScriptedAdapter

        adapter = ScriptedAdapter([patch_reply()])
        from realtask.evidence import RunDirectory

        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = BenchmarkRunner(
            adapter,
            RunDirectory(self.tmp / "trunc", "t"),
            RunnerOptions(max_source_bytes=1024),
            work_root=self.tmp / "truncwork",
            harness_root=REPO_ROOT,
        )
        runner.run_task(task, ["single"])
        runner.close()
        prompt = adapter.requests[0].user
        self.assertIn("(TRUNCATED)", prompt)

    def test_no_truncation_when_the_budget_is_generous(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = self.runner(
            [patch_reply()], options=RunnerOptions(max_source_bytes=10_000_000)
        )
        result = runner.run_task(task, ["single"])
        self.assertFalse(
            any("truncated" in note for note in result.attempts[0].metrics.notes)
        )


class MultipleSingleAttemptTests(HarnessTestCase):
    def test_several_single_attempts_all_run(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX, [patch_reply(), patch_reply(), patch_reply()],
            ["single"], single_attempts=3,
        )
        self.assertEqual(len(result.attempts), 3)
        for state in result.attempts:
            self.assertOutcome(state, Outcome.SUCCESS)

    def test_comparison_picks_the_best_of_several_singles(self):
        from test_realtask_support import NON_APPLYING_PATCH

        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [patch_reply(NON_APPLYING_PATCH), patch_reply(), patch_reply()],
            ["single"], single_attempts=3,
        )
        from realtask.compare import build_comparison

        comparison = build_comparison(
            "t", "bug_fix", "f", [s.metrics for s in result.attempts], None
        )
        self.assertEqual(len(comparison["single_attempts_considered"]), 3)
        self.assertEqual(
            len({m["attempt_id"] for m in comparison["single_attempts_considered"]}), 3,
            "each single attempt must have its own id",
        )
        self.assertEqual(comparison["best_single"]["quality"]["outcome"], "SUCCESS")
        chosen = [m for m in comparison["single_attempts_considered"]
                  if m["attempt_id"] == comparison["best_single"]["attempt_id"]]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["outcome"], "SUCCESS")
        self.assertIn("failure-rank", comparison["best_single_selection_rule"])


class RequireHeadTests(HarnessTestCase):
    def test_require_head_on_a_snapshot_without_git_fails_closed(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = self.runner(
            [patch_reply()], options=RunnerOptions(require_head=True)
        )
        result = runner.run_task(task, ["single"])
        state = result.attempts[0]
        self.assertOutcome(state, Outcome.SOURCE_MISMATCH)
        self.assertEqual(state.metrics.model_calls, 0)
        self.assertTrue(
            any("not itself a git repository root" in reason
                for reason in state.metrics.notes),
            list(state.metrics.notes),
        )

    def test_without_require_head_the_snapshot_is_accepted(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        runner = self.runner([patch_reply()], options=RunnerOptions(require_head=False))
        result = runner.run_task(task, ["single"])
        self.assertOutcome(result.attempts[0], Outcome.SUCCESS)


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


class SwarmEarnsItsCostTests(HarnessTestCase):
    """The harness's central claim, end to end: a second role changes the result.

    This repository is a multi-agent harness whose stated purpose is to measure
    whether role separation is worth its overhead. Nothing demonstrated that. The
    swarm tests covered ordering, per-call charging, rejection labels and the
    refinement budget -- all of them asserted on *scripted* behaviour, and all of
    them stopped before the interesting state. No test had a reviewer reject a
    candidate and a refinement then succeed. The loop the whole design rests on
    was unexercised.

    The trap is the fixture's own designed one. ``test_realtask_trap_dropped_driver.diff``
    is a clean, plausible fix: it moves planning inside the driver's live scope,
    which is the actual defect, and it keeps close-once on every path. It also
    drops ``driver=driver``, on the reasonable reading that the driver is already
    in scope. The consequence is silent -- graph mining stops and the command
    falls back to templated text -- and the fixture's oracle rejects it.

    A single attempt takes that bait. A swarm gets a second opinion, and the
    revision is right. That is the claim, and it is worth having in a test
    because it is the one thing the harness exists to be able to say.
    """

    TRAP = "test_realtask_trap_dropped_driver.diff"

    def trap_reply(self):
        from test_realtask_support import REPO_ROOT, patch_reply

        return patch_reply((REPO_ROOT / self.TRAP).read_text(encoding="utf-8"))

    def test_a_single_attempt_takes_the_bait_and_the_swarm_does_not(self):
        from test_realtask_support import AUTO_INGEST_BUG_FIX, patch_reply
        from test_realtask_support import REFERENCE_REPAIR, scout_reply

        trap = self.trap_reply()

        # One pass: plausible fix, silently wrong. `single` is a single
        # Role.SINGLE call -- no scout -- so the bait goes in first.
        _t, single = self.run_stages(AUTO_INGEST_BUG_FIX, [trap], ["single"])
        single_state = single.attempts[0]
        self.assertFalse(
            single_state.metrics.tests.targeted_all_passed,
            "the trap patch was accepted, so this test proves nothing",
        )

        # Same first attempt, plus a reviewer and one revision.
        _t, both = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [
                trap,
                scout_reply(), trap,
                review_reply(
                    "revise",
                    [{
                        "severity": "high",
                        "location": "auto_ingest/shorts/cli.py:_cmd_plan",
                        "description": (
                            "planning is now inside the live scope, but the "
                            "driver argument was dropped, so graph mining stops "
                            "and the command silently falls back to templated text"
                        ),
                    }],
                ),
                patch_reply(REFERENCE_REPAIR),
            ],
            ["single", "swarm"],
        )
        single_state, swarm_state = both.attempts

        roles = [c.role.value for c in swarm_state.metrics.calls]
        self.assertEqual(
            roles, ["scout", "implementer", "reviewer", "implementer"],
            "the swarm should scout, implement, review, then revise",
        )
        self.assertEqual(swarm_state.metrics.refinements_used, 1)
        self.assertTrue(
            swarm_state.metrics.tests.targeted_all_passed,
            "the revised patch should satisfy the oracle: {}".format(
                [c.stdout[-400:] for c in swarm_state.metrics.tests.targeted_failed]
            ),
        )
        self.assertOutcome(swarm_state, Outcome.SUCCESS)

    def test_the_extra_roles_are_paid_for_in_the_record(self):
        """Earning the result must not hide what it cost.

        The comparison artifact exists to answer whether separation is worth the
        overhead. If a swarm could reach SUCCESS without the artifact showing
        that it spent three more calls to get there, the artifact would be
        reporting the outcome and suppressing the cost.
        """
        from test_realtask_support import AUTO_INGEST_BUG_FIX, REFERENCE_REPAIR, scout_reply

        _t, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [
                self.trap_reply(),
                scout_reply(), self.trap_reply(),
                review_reply("revise", [{
                    "severity": "high",
                    "location": "auto_ingest/shorts/cli.py:_cmd_plan",
                    "description": "the driver argument was dropped, so mining is lost",
                }]),
                patch_reply(REFERENCE_REPAIR),
            ],
            ["single", "swarm"],
        )
        single_state, swarm_state = result.attempts
        single_calls = len(single_state.metrics.calls)
        swarm_calls = len(swarm_state.metrics.calls)
        self.assertGreater(
            swarm_calls, single_calls,
            "the swarm spent {} calls against the single's {}; role separation "
            "that costs nothing would mean the cost model is wrong".format(
                swarm_calls, single_calls
            ),
        )
        # The record is metrics.json, which run_task writes; comparison.json is
        # emitted one layer up by the CLI, and is covered by its own tests.
        payload = json.loads(
            result.run_dir.file("metrics.json").read_text(encoding="utf-8")
        )
        recorded = [len(a["calls"]) for a in payload["attempts"]]
        self.assertEqual(
            recorded, sorted(recorded),
            "expected the single attempt first, then the costlier swarm",
        )
        self.assertEqual(recorded[0], single_calls)
        self.assertEqual(recorded[1], swarm_calls)
        self.assertGreater(recorded[1], recorded[0])
        # and every attempt still says where it came from
        for state in (single_state, swarm_state):
            self.assertEqual(state.metrics.candidate_source, "model")


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


class ToolCallClassificationTests(HarnessTestCase):
    """A reply that calls a tool is a different fact from one that ran long.

    Added after the first live run against a tool-tuned model, which answered
    with a 900-token ``<tool_call name="read_file">``. The attempt was filed as
    TRUNCATED, which is true -- but the note said "contained no JSON object", so
    the evidence sent an operator to the token budget when the actual cause was
    that the role contract's "you have NO tools" had been ignored.
    """

    def reply(self, content):
        from realtask.adapter import ScriptedResponse

        return ScriptedResponse(content=content, finish_reason="length")

    def test_a_tool_call_is_recorded_as_its_own_outcome(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [self.reply('<tool_call name="read_file" call_id="aaaa">')],
            ["single"],
        )
        state = result.attempts[0]
        seen = [o.value for o in state.metrics.outcomes_seen]
        self.assertIn(Outcome.TOOL_CALL_REQUESTED.value, seen)

    def test_the_note_names_the_tool_and_says_why_it_is_unavailable(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [self.reply('<tool_call name="read_file" call_id="aaaa">')],
            ["single"],
        )
        notes = " ".join(result.attempts[0].metrics.notes)
        self.assertIn("read_file", notes)
        self.assertIn("read-only", notes)

    def test_truncation_is_still_reported_when_it_also_happened(self):
        """A reply can be both; the outcome precedence decides, not the guess."""
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [self.reply('<tool_call name="write_file" call_id="aaaa">')],
            ["single"],
        )
        seen = [o.value for o in result.attempts[0].metrics.outcomes_seen]
        self.assertIn(Outcome.TOOL_CALL_REQUESTED.value, seen)
        self.assertIn(Outcome.TRUNCATED.value, seen)

    def test_the_headline_outcome_is_the_diagnosis_not_the_symptom(self):
        """A tool call ranks ahead of the truncation it causes.

        The harness orders outcomes by which fact is actionable. "The model asked
        for a tool that does not exist here" is the diagnosis; "it ran out of
        tokens" is what happened while it was doing that.
        """
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [self.reply('<tool_call name="read_file" call_id="aaaa">')],
            ["single"],
        )
        metrics = result.attempts[0].metrics
        self.assertEqual(metrics.outcome, Outcome.TOOL_CALL_REQUESTED)
        self.assertEqual(
            metrics.outcome.value,
            result.attempts[0].metrics.outcomes_seen[0].value,
            "the highest-precedence outcome should be the one reported",
        )

    def test_a_plain_json_reply_is_not_mistaken_for_a_tool_call(self):
        """Detection must not fire on ordinary prose that mentions tools."""
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [ScriptedResponse(
                content='{"diff": "diff --git a/x b/x\n", "notes": '
                        '"no tool call here", "confidence": 0.5}')],
            ["single"],
        )
        seen = [o.value for o in result.attempts[0].metrics.outcomes_seen]
        self.assertNotIn(Outcome.TOOL_CALL_REQUESTED.value, seen)


class RefinementEvidenceTests(HarnessTestCase):
    """The safety verdict in the evidence must describe the candidate that ran.

    Found by scripting a swarm whose reviewer asks for changes and whose
    refinement then smuggles a symlink past the screen. The screen caught it --
    no symlink reached any worktree -- but the evidence contradicted itself:

        apply_reason   "patch declares non-regular file mode(s) 120000; ..."
        safety_ok      False
        safety_reason  ''

    ``_merge_review`` copied a hand-listed set of patch fields from the review
    attempt into the swarm attempt and ``safety_ok`` / ``safety_reason`` were not
    on the list, so the swarm kept the *first* candidate's safety state. Every
    other field correctly described the final candidate.
    """

    SYMLINK_PATCH = (
        "diff --git a/_realtask_tests/t.py b/_realtask_tests/t.py\n"
        "new file mode 120000\n"
        "--- /dev/null\n"
        "+++ b/_realtask_tests/t.py\n"
        "@@ -0,0 +1 @@\n"
        "+/etc/passwd\n"
    )

    def refinement_reply(self, patch: str):
        import json

        from realtask.adapter import ScriptedResponse

        return ScriptedResponse(content=json.dumps(
            {"patch": patch, "tests": [], "assumptions": [], "confidence": 0.5}))

    def run_swarm_with_symlink_refinement(self):
        from realtask.adapter import ScriptedResponse

        return self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [
                scout_reply(),
                patch_reply(),
                review_reply("revise", defects=[{
                    "severity": "blocker", "location": "cli.py:71",
                    "description": "planning after close"}]),
                self.refinement_reply(self.SYMLINK_PATCH),
                review_reply("accept"),
            ],
            ["swarm"],
        )

    def test_a_symlink_in_the_refinement_is_still_screened(self):
        _task, result = self.run_swarm_with_symlink_refinement()
        metrics = result.attempts[0].metrics
        self.assertEqual(metrics.outcome, Outcome.INVALID_PATCH)
        self.assertFalse(metrics.patch.safety_ok)
        self.assertEqual(metrics.refinements_used, 1)

    def test_the_evidence_explains_the_refinement_refusal(self):
        """The defect: apply_reason explained it while safety_reason was empty."""
        _task, result = self.run_swarm_with_symlink_refinement()
        patch = result.attempts[0].metrics.patch
        self.assertFalse(patch.safety_ok)
        self.assertTrue(
            patch.safety_reason,
            "safety_reason is empty, so the evidence contradicts apply_reason "
            "({!r}) about a containment decision".format(patch.apply_reason[:80]),
        )
        self.assertIn("120000", patch.safety_reason)
        self.assertIn("non-regular", patch.safety_reason)

    def test_safety_fields_agree_with_the_applied_candidate(self):
        """Whatever the verdict, it must be the one for the validated candidate."""
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(), review_reply("accept")],
            ["swarm"],
        )
        patch = result.attempts[0].metrics.patch
        self.assertTrue(patch.applied)
        self.assertTrue(
            patch.safety_ok,
            "a clean first candidate must not inherit a refusal from nowhere",
        )
        self.assertEqual(patch.safety_reason, "")


class RefinementCeilingTests(unittest.TestCase):
    """The refinement budget is a ceiling, and it had no test at all.

    The document says ``--max-refinements`` "can lower it and can never raise
    it". That is enforced by ``RunnerOptions.clamp`` and by a second clamp in the
    CLI, and nothing checked either. A refactor that dropped one would make a
    documented safety ceiling silently raisable, and the harness would then spend
    unbounded model calls on a single attempt.
    """

    def test_clamp_lowers_but_never_raises(self):
        from realtask.runner import RunnerOptions
        from realtask.version import MAX_REFINEMENTS

        self.assertEqual(MAX_REFINEMENTS, 1)
        for asked, expected in ((0, 0), (1, 1), (2, 1), (5, 1), (99, 1)):
            with self.subTest(asked=asked):
                self.assertEqual(
                    RunnerOptions(max_refinements=asked).clamp().max_refinements,
                    expected,
                )

    def test_the_cli_clamps_too(self):
        """Two independent clamps exist; both are load-bearing."""
        import argparse

        import realtime_bench

        parser = realtime_bench.build_parser()
        for asked in (2, 50):
            args = parser.parse_args(
                ["run", "--base-url", "http://x/v1", "--model", "m",
                 "--max-refinements", str(asked)]
            )
            self.assertGreater(args.max_refinements, 1)
        # The clamp itself lives where the options are assembled.
        source = (REPO_ROOT / "realtime_bench.py").read_text(encoding="utf-8")
        self.assertIn("min(args.max_refinements, MAX_REFINEMENTS)", source)


class EvidenceRootInsideCheckoutTests(HarnessTestCase):
    """The documented default invocation must be able to evaluate a patch.

    ``--out`` defaults to ``<repo>/runs`` and the operator example in the
    document uses ``--out ./runs``. But ``EvaluationWorktree`` refuses to build a
    disposable copy inside the harness checkout, the fixture, or the bound source
    -- correctly, and the scratch worktree was derived from the evidence root. So
    every stage that actually *evaluates* a patch failed with
    ``WorktreeGuardError`` under the documented invocation.

    Nothing caught it. Every test puts its run directory in a temporary directory
    outside the repository, and every live attempt so far died at role parsing
    before ``evaluate`` was reached -- so this was the first time the live path
    got as far as creating a worktree.

    The guard stays absolute. The runner refuses to aim at it: the scratch root
    moves to a temporary directory and the move is recorded in the evidence,
    because where the evaluation copy lived is part of the record.
    """

    def runner_inside_the_checkout(self, responses):
        from realtask.runner import BenchmarkRunner, RunnerOptions

        evidence = REPO_ROOT / "runs" / "realtask-test-evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, evidence, True)
        runner = BenchmarkRunner(
            ScriptedAdapter(responses),
            self.run_dir,
            RunnerOptions(test_timeout_s=180.0),
            # Deliberately inside the guarded checkout, as the default is.
            work_root=evidence / "work",
            harness_root=REPO_ROOT,
        )
        self.addCleanup(runner.close)
        return runner

    def test_a_work_root_inside_the_checkout_is_relocated_not_refused(self):
        runner = self.runner_inside_the_checkout([patch_reply()])
        runner.run_task(self.task(AUTO_INGEST_BUG_FIX), ["single"])
        self.assertIsNotNone(
            runner.work_root_relocated_from,
            "the work root was left inside the guarded checkout",
        )
        self.assertNotIn(
            REPO_ROOT.resolve(),
            runner.work_root.resolve().parents,
            "the relocated work root is still inside the harness checkout",
        )

    def test_the_relocation_is_recorded_on_the_attempt(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"]
        )
        # Baseline: the normal temporary-directory case records nothing.
        notes = " ".join(result.attempts[0].metrics.notes)
        self.assertNotIn("evaluation work root moved", notes)

    def test_evaluation_actually_runs_with_an_evidence_root_in_the_checkout(self):
        """The property that matters: the documented default reaches acceptance."""
        runner = self.runner_inside_the_checkout([patch_reply()])
        result = runner.run_task(self.task(AUTO_INGEST_BUG_FIX), ["single"])
        metrics = result.attempts[0].metrics
        self.assertTrue(metrics.patch.applied, metrics.patch.apply_reason)
        self.assertGreater(
            len(metrics.tests.targeted), 0,
            "acceptance never ran; the documented --out cannot evaluate a patch",
        )
        self.assertTrue(metrics.tests.targeted_all_passed)

    def test_the_guard_itself_is_untouched(self):
        """Relocating the aim must not weaken the refusal."""
        import tempfile

        from realtask.evaluation import EvaluationWorktree, WorktreeGuardError

        task = self.task(AUTO_INGEST_BUG_FIX)
        with tempfile.TemporaryDirectory() as tmp:
            # A scratch tree that is genuinely guarded, so this exercises the
            # refusal rather than pointing at the real fixture and risking a
            # stray directory inside a sealed snapshot.
            guarded = Path(tmp) / "read-only"
            guarded.mkdir()
            inside = guarded / "work"
            with self.assertRaises(WorktreeGuardError):
                EvaluationWorktree.create(task, inside, guard_paths=[guarded])

    def test_no_worktree_is_left_inside_the_checkout(self):
        runner = self.runner_inside_the_checkout([patch_reply()])
        runner.run_task(self.task(AUTO_INGEST_BUG_FIX), ["single"])
        for worktree in runner._worktrees:
            self.assertNotIn(
                REPO_ROOT.resolve(),
                worktree.root.resolve().parents,
                "an evaluation worktree was created inside the checkout",
            )


if __name__ == "__main__":
    unittest.main()


class CandidateProvenanceTests(HarnessTestCase):
    """An operator's patch must never read as a model's result.

    ``--scout-file`` records where a recorded scout came from. ``--patch-file``
    recorded nothing at all, so an attempt a human solved by hand and an attempt a
    model solved were indistinguishable in ``metrics.json``, ``comparison.json``
    and the roll-up.

    That is not hypothetical: a live ``--stage review`` run with a hand-written
    reference patch scored targeted 1/1 and broader 1/1. Pooled, that is a
    benchmark score nobody earned.
    """

    def operator_run(self):
        from test_realtask_support import (
            AUTO_INGEST_BUG_FIX, REFERENCE_REPAIR, ScriptedResponse,
        )

        return self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [ScriptedResponse(content="```diff\n" + REFERENCE_REPAIR + "\n```")],
            ["review"],
            patch_override=REFERENCE_REPAIR,
        )

    def test_an_operator_candidate_is_marked_as_such(self):
        _task, result = self.operator_run()
        metrics = result.attempts[0].metrics
        self.assertEqual(metrics.candidate_source, "operator_patch_file")

    def test_the_marker_survives_into_the_written_artifact(self):
        _task, result = self.operator_run()
        self.assertEqual(
            result.attempts[0].metrics.to_dict()["candidate_source"],
            "operator_patch_file",
        )

    def test_a_model_candidate_is_marked_as_the_model_s(self):
        _task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        metrics = result.attempts[0].metrics
        self.assertEqual(metrics.candidate_source, "model")
        self.assertEqual(metrics.to_dict()["candidate_source"], "model")

    def test_the_attempt_says_it_says_nothing_about_the_model(self):
        _task, result = self.operator_run()
        joined = " ".join(result.attempts[0].metrics.notes)
        self.assertIn("--patch-file", joined)
        self.assertIn("says nothing about model ability", joined)

    def test_the_marker_survives_the_review_merge_path(self):
        """The review path is where a hand-maintained merge list lost fields once."""
        from test_realtask_support import ScriptedResponse

        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [ScriptedResponse(content="```diff\n" + REFERENCE_REPAIR + "\n```")],
            ["review"],
            patch_override=REFERENCE_REPAIR,
        )
        for attempt in result.attempts:
            self.assertEqual(
                attempt.metrics.candidate_source, "operator_patch_file",
                "{} lost its candidate provenance".format(attempt.attempt_id),
            )

    def test_no_attempt_reaches_the_artifact_without_an_origin(self):
        for stage in ("single", "swarm", "scout", "implement"):
            with self.subTest(stage=stage):
                responses = {
                    "single": [patch_reply()],
                    "swarm": [scout_reply(), patch_reply(), review_reply("accept")],
                    "scout": [scout_reply()],
                    "implement": [patch_reply()],
                }[stage]
                _task, result = self.run_stages(
                    AUTO_INGEST_BUG_FIX, responses, [stage]
                )
                for attempt in result.attempts:
                    self.assertIn(
                        attempt.metrics.to_dict().get("candidate_source"),
                        ("model", "operator_patch_file"),
                    )


if __name__ == "__main__":
    unittest.main()


class ReplicationTests(HarnessTestCase):
    """A comparison of one sample against one sample is not a measurement.

    The artifact already said one *task* is not evidence that role separation
    helps. It said nothing about one *sample* -- and the swarm side could not even
    be repeated, so every single-vs-swarm comparison this harness produced was
    structurally n=1 on the interesting side. The difference between "role
    separation helped" and "that run went better" is unobservable at n=1.
    """

    def test_the_swarm_side_can_be_repeated(self):
        task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(), review_reply("accept")] * 2,
            ["swarm"],
            swarm_attempts=2,
        )
        swarm = [a for a in result.attempts if a.metrics.strategy == "swarm"]
        self.assertEqual(len(swarm), 2)
        self.assertEqual(
            [a.attempt_id for a in swarm],
            [AUTO_INGEST_BUG_FIX + "::swarm", AUTO_INGEST_BUG_FIX + "::swarm#2"],
            "repeated attempts must be individually addressable",
        )

    def test_one_swarm_by_default(self):
        task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(), review_reply("accept")],
            ["swarm"],
        )
        self.assertEqual(
            len([a for a in result.attempts if a.metrics.strategy == "swarm"]), 1
        )

    def test_single_attempts_still_work(self):
        task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX, [patch_reply(), patch_reply()], ["single"],
            single_attempts=2,
        )
        self.assertEqual(
            len([a for a in result.attempts if a.metrics.strategy == "single"]), 2
        )


if __name__ == "__main__":
    unittest.main()
