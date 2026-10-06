"""Roll-up tests: the cross-task artifact a controller consumes.

The stance matches realtask.compare and realtask.evidence: report what happened,
keep every component addressable, assert nothing about whether it is good.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from realtask.summarize import summarize_runs
from realtask.taxonomy import Outcome
from realtask.version import SCHEMA_METRICS, SCHEMA_ROLLUP, SCHEMA_RUN_MANIFEST

PATCH = {"applied": True, "files_changed": ["a.py"], "unnecessary_changed_files": [],
         "syntax_ok": True}
TESTS = {"targeted_passed": 1, "targeted_total": 1, "broader_passed": None,
         "broader_total": 0}


def manifest(run_id, sha="a" * 40, integrity=None, schema=SCHEMA_RUN_MANIFEST):
    return {
        "schema": schema,
        "run_id": run_id,
        "harness": {"name": "realtask", "git_sha": sha, "git_dirty": False},
        "fixture": {"task_id": "t", "task_family": "bug_fix", "fixture_sha256": "f" * 64},
        "model_runtime": [{"label": "lab", "node": "n", "model": "m"}],
        "strategies": ["single"],
        "integrity": integrity or {"authoritative_source_unchanged": True},
    }


def metrics(task_id, family, attempts):
    return {
        "schema": SCHEMA_METRICS,
        "task_id": task_id,
        "task_family": family,
        "fixture_sha256": "f" * 64,
        "manifest_sha256": "m" * 64,
        "snapshot_sha256": "s" * 64,
        "source_binding_sha256": "b" * 64,
        "source_head": "d" * 40,
        "attempts": attempts,
    }


def attempt(attempt_id, strategy, outcome, **overrides):
    row = {
        "attempt_id": attempt_id,
        "strategy": strategy,
        "outcome": outcome,
        "task_success": outcome == Outcome.SUCCESS.value,
        "model_calls": 1,
        "model_wall_s": 1.0,
        "harness_overhead_s": 0.1,
        "total_wall_s": 1.1,
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "total_tokens": 110,
        "refinements_used": 0,
        "refinement_budget": 1,
        "harness_error": None,
        # Synthetic attempts are model attempts; real metrics.json says so too.
        "candidate_source": "model",
        "grounding": {"score": 0.75},
        "patch": dict(PATCH),
        "tests": dict(TESTS),
        "review": {"verdict": None, "defects": []},
    }
    row.update(overrides)
    return row


class RollupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="realtask-rollup-")
        self.runs = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write_run(self, run_id, manifest_payload, metrics_payload=None, nested=None):
        run_dir = self.runs / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps(manifest_payload, indent=2))
        if metrics_payload is not None:
            (run_dir / "metrics.json").write_text(json.dumps(metrics_payload, indent=2))
        for task_id, payload in (nested or {}).items():
            target = run_dir / "tasks" / task_id
            target.mkdir(parents=True, exist_ok=True)
            (target / "metrics.json").write_text(json.dumps(payload, indent=2))
        return run_dir

    def seed(self):
        self.write_run(
            "r1",
            manifest("r1"),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.SUCCESS.value),
                attempt("t1::swarm", "swarm", Outcome.SUCCESS.value, model_calls=3),
            ]),
        )
        return summarize_runs(self.runs).to_dict()


class SingleRunTests(RollupTestCase):
    def test_single_run_is_summarised(self):
        payload = self.seed()
        self.assertEqual(payload["schema"], SCHEMA_ROLLUP)
        self.assertEqual(payload["run_ids"], ["r1"])
        agg = payload["aggregate"]
        self.assertEqual(agg["runs"], 1)
        self.assertEqual(agg["tasks"], 1)
        self.assertEqual(agg["attempts"], 2)
        self.assertEqual(agg["outcome_histogram"], {"SUCCESS": 2})
        self.assertEqual(agg["by_strategy"]["swarm"]["model_calls"], 3)
        self.assertEqual(agg["totals"]["total_tokens"], 220)

    def test_component_metrics_are_retained_per_attempt(self):
        self.write_run(
            "r1",
            manifest("r1"),
            metrics("t1", "bug_fix", [
                attempt(
                    "t1::single", "single", Outcome.TARGETED_TEST_FAILURE.value,
                    patch={"applied": True, "files_changed": ["a.py"],
                           "unnecessary_changed_files": ["b.py"], "syntax_ok": True},
                    tests={"targeted_passed": 0, "targeted_total": 1,
                           "broader_passed": None, "broader_total": 0},
                    review={"verdict": "revise", "defects": [{"severity": "high"}]},
                ),
            ]),
        )
        row = summarize_runs(self.runs).to_dict()["tasks"][0]["attempts"][0]
        self.assertEqual(row["grounding_score"], 0.75)
        self.assertTrue(row["patch_applied"])
        self.assertEqual(row["unnecessary_changed_files"], 1)
        self.assertEqual(row["targeted_passed"], 0)
        self.assertEqual(row["review_verdict"], "revise")
        self.assertEqual(row["review_defects"], 1)

    def test_task_identity_is_preserved(self):
        self.seed()
        row = summarize_runs(self.runs).to_dict()["tasks"][0]
        self.assertEqual(row["task_id"], "t1")
        self.assertEqual(row["fixture_sha256"], "f" * 64)
        self.assertEqual(row["source_head"], "d" * 40)


class MultiRunTests(RollupTestCase):
    def test_families_are_reported_separately(self):
        self.write_run(
            "r1", manifest("r1"),
            metrics("t1", "bug_fix",
                    [attempt("t1::single", "single", Outcome.SUCCESS.value)]),
        )
        self.write_run(
            "r2", manifest("r2", sha="b" * 40),
            metrics("t2", "code_review",
                    [attempt("t2::single", "single", Outcome.REVIEW_REJECTED.value)]),
        )
        payload = summarize_runs(self.runs).to_dict()
        agg = payload["aggregate"]
        self.assertEqual(agg["runs"], 2)
        self.assertEqual(agg["tasks"], 2)
        self.assertEqual(agg["outcome_histogram"],
                         {"REVIEW_REJECTED": 1, "SUCCESS": 1})
        self.assertEqual(agg["by_task_family"]["bug_fix"]["successful_tasks"], 1)
        self.assertEqual(agg["by_task_family"]["code_review"]["successful_tasks"], 0)
        self.assertEqual(payload["harness_git_shas"], ["a" * 40, "b" * 40])

    def test_nested_multi_task_runs_are_flattened(self):
        self.write_run(
            "r1", manifest("r1"), nested={
                "t1": metrics("t1", "bug_fix",
                              [attempt("t1::single", "single", Outcome.SUCCESS.value)]),
                "t2": metrics("t2", "test_generation",
                              [attempt("t2::single", "single", Outcome.SUCCESS.value)]),
            },
        )
        payload = summarize_runs(self.runs).to_dict()
        self.assertEqual(payload["aggregate"]["tasks"], 2)
        self.assertEqual(sorted(t["task_id"] for t in payload["tasks"]), ["t1", "t2"])

    def test_identity_is_deduplicated_across_runs(self):
        self.write_run("r1", manifest("r1"), metrics("t1", "bug_fix", []))
        self.write_run("r2", manifest("r2"), metrics("t2", "bug_fix", []))
        self.assertEqual(len(summarize_runs(self.runs).to_dict()["model_runtimes"]), 1)


class HonestyTests(RollupTestCase):
    def test_no_composite_score_is_emitted(self):
        payload = self.seed()
        self.assertIsNone(payload["composite_score"])
        self.assertTrue(payload["component_metrics_retained"])
        blob = json.dumps(payload).lower()
        # "qualifies" appears in the disclaimer on purpose; what must not exist is
        # an artifact that asserts a qualification verdict.
        for banned in ("\"qualifies\": true", "qualified_model", "pass_rate",
                       "overall_score", "better_than", "recommended_model",
                       "swarm_better", "\"verdict\":"):
            self.assertNotIn(banned, blob, banned)

    def test_scope_limits_are_stated_in_the_artifact(self):
        joined = " ".join(self.seed()["scope_limits"])
        self.assertIn("does not establish that a model qualifies", joined)
        self.assertIn("not a difficulty weighting", joined)
        self.assertIn("never estimated", joined)

    def test_tokens_are_null_when_any_attempt_lacks_usage(self):
        self.write_run(
            "r1", manifest("r1"),
            metrics("t1", "bug_fix", [
                attempt("a", "single", Outcome.SUCCESS.value),
                attempt("b", "single", Outcome.EMPTY_OUTPUT.value, total_tokens=None,
                        prompt_tokens=None, completion_tokens=None),
            ]),
        )
        totals = summarize_runs(self.runs).to_dict()["aggregate"]["totals"]
        self.assertIsNone(totals["total_tokens"])
        self.assertIsNone(totals["prompt_tokens"])
        self.assertEqual(
            totals["model_calls"], 2.0,
            "call counts are exact and must not be nulled by a token gap",
        )

    def test_empty_runs_root_is_not_an_error_but_is_flagged(self):
        payload = summarize_runs(self.runs / "nonexistent").to_dict()
        self.assertEqual(payload["aggregate"]["runs"], 0)
        self.assertEqual(payload["tasks"], [])


class MixedHarnessRevisionTests(RollupTestCase):
    """A roll-up must not present cross-revision totals as like-for-like.

    The harness already refuses to pool across artifact *schemas*, so a legacy
    ``bench_*`` run can never be silently mixed into a ``realtask.*.v1`` total.
    The same discipline was missing one level in: revisions *within* a schema.

    That is not theoretical here. Nine call sites once billed the model's own
    latency to ``harness_overhead_s``. A roll-up spanning that fix sums a number
    that meant one thing before it and another after, and nothing said so. Found
    by running ``summarize`` over the first real evidence directories, where the
    totals carried 53.95s of harness overhead that was really model latency.
    """

    def test_one_revision_is_not_reported_as_mixed(self):
        self.seed()
        payload = summarize_runs(self.runs).to_dict()
        self.assertFalse(payload["mixed_harness_revisions"])
        self.assertFalse(
            any("different harness revisions" in lim for lim in payload["scope_limits"]),
            "a single-revision roll-up must not carry the mixing warning",
        )

    def test_two_revisions_are_flagged_and_explained(self):
        self.write_run(
            "r1",
            manifest("r1", sha="a" * 40),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.SUCCESS.value)]),
        )
        self.write_run(
            "r2",
            manifest("r2", sha="b" * 40),
            metrics("t2", "bug_fix", [
                attempt("t2::single", "single", Outcome.SUCCESS.value)]),
        )
        payload = summarize_runs(self.runs).to_dict()
        self.assertTrue(payload["mixed_harness_revisions"])
        self.assertEqual(len(payload["harness_git_shas"]), 2)
        joined = " ".join(payload["scope_limits"])
        self.assertIn("different harness revisions", joined)
        self.assertIn("a" * 12, joined)
        self.assertIn("b" * 12, joined)
        self.assertIn("rather than a like-for-like measurement", joined)

    def test_the_flag_is_structured_not_only_prose(self):
        """A controller should be able to check this without parsing English."""
        self.write_run(
            "r1", manifest("r1", sha="a" * 40),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.SUCCESS.value)]),
        )
        self.write_run(
            "r2", manifest("r2", sha="b" * 40),
            metrics("t2", "bug_fix", [
                attempt("t2::single", "single", Outcome.SUCCESS.value)]),
        )
        payload = summarize_runs(self.runs).to_dict()
        self.assertIs(payload["mixed_harness_revisions"], True)

    def test_the_standing_limits_still_apply_when_mixed(self):
        """The new limit is additive, not a replacement."""
        self.write_run(
            "r1", manifest("r1", sha="a" * 40),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.SUCCESS.value)]),
        )
        self.write_run(
            "r2", manifest("r2", sha="b" * 40),
            metrics("t2", "bug_fix", [
                attempt("t2::single", "single", Outcome.SUCCESS.value)]),
        )
        payload = summarize_runs(self.runs).to_dict()
        joined = " ".join(payload["scope_limits"])
        self.assertIn("does not establish that a model qualifies", joined)
        self.assertIn("never estimated and never partially summed", joined)
        self.assertIn("belongs to an authoritative controller", joined)


class RobustnessTests(RollupTestCase):
    def test_a_corrupt_manifest_is_skipped_not_fatal(self):
        self.seed()
        broken = self.runs / "r2"
        broken.mkdir()
        (broken / "manifest.json").write_text("{not json")
        payload = summarize_runs(self.runs).to_dict()
        self.assertEqual(len(payload["run_ids"]), 1)
        self.assertTrue(any("unreadable manifest" in s["reason"] for s in payload["skipped_runs"]))

    def test_a_foreign_manifest_is_skipped(self):
        self.seed()
        foreign = self.runs / "r3"
        foreign.mkdir()
        (foreign / "manifest.json").write_text(json.dumps({"schema": "bench_v7.unversioned"}))
        payload = summarize_runs(self.runs).to_dict()
        self.assertNotIn("r3", payload["run_ids"])
        self.assertTrue(any("manifest schema" in s["reason"] for s in payload["skipped_runs"]))

    def test_a_run_with_no_metrics_is_recorded_as_skipped(self):
        self.write_run("r1", manifest("r1"))
        payload = summarize_runs(self.runs).to_dict()
        self.assertTrue(any("no metrics.json" in s["reason"] for s in payload["skipped_runs"]))

    def test_a_rollup_file_in_the_runs_root_is_ignored(self):
        self.seed()
        (self.runs / "rollup.json").write_text(json.dumps({"schema": SCHEMA_ROLLUP}))
        self.assertEqual(len(summarize_runs(self.runs).to_dict()["run_ids"]), 1)

    def test_integration_failures_and_harness_errors_are_surfaced(self):
        self.write_run(
            "r1",
            manifest(
                "r1",
                integrity={"authoritative_source_unchanged": False,
                           "harness_errors": ["t1::swarm"]},
            ),
            metrics("t1", "bug_fix", [
                attempt("t1::swarm", "swarm", Outcome.PROTOCOL_FAILURE.value,
                        harness_error="RuntimeError: boom"),
            ]),
        )
        payload = summarize_runs(self.runs).to_dict()
        self.assertEqual(payload["runs_with_integrity_failures"], ["r1"])
        self.assertEqual(payload["harness_errors"], ["t1::swarm"])
        self.assertEqual(
            payload["tasks"][0]["attempts"][0]["harness_error"], "RuntimeError: boom"
        )


class LegacyArtifactCandidateSourceTests(RollupTestCase):
    """An artifact written before provenance existed is unknown, not "model".

    Found while adding the field: older ``metrics.json`` files have no
    ``candidate_source`` key at all, and the first version of the rollup crashed
    sorting ``None``. Bucketing those as ``"model"`` would be the wrong guess in
    exactly the direction that flatters a result -- the missing value could just as
    easily have been an operator-supplied patch.
    """

    def test_an_absent_source_is_bucketed_as_unknown(self):
        self.write_run(
            "r1",
            manifest("r1"),
            metrics("t1", "bug_fix", [
                {"attempt_id": "t1::single", "strategy": "single",
                 "outcome": Outcome.SUCCESS.value, "model_calls": 1}]),
        )
        payload = summarize_runs(self.runs).to_dict()
        self.assertEqual(payload["attempts_by_candidate_source"], {"unknown": 1})
        joined = " ".join(payload["scope_limits"])
        self.assertIn("predate candidate-source provenance", joined)
        self.assertIn("rather than assumed to be", joined)

    def test_unknown_does_not_claim_to_be_a_model_attempt(self):
        self.write_run(
            "r1",
            manifest("r1"),
            metrics("t1", "bug_fix", [
                {"attempt_id": "t1::single", "strategy": "single",
                 "outcome": Outcome.SUCCESS.value, "model_calls": 1}]),
        )
        payload = summarize_runs(self.runs).to_dict()
        self.assertNotIn("model", payload["attempts_by_candidate_source"])


if __name__ == "__main__":
    unittest.main()


class CandidateSourceRollupTests(RollupTestCase):
    """A roll-up must not count an operator's patch as a model's result.

    Found by running the harness for real: a ``--stage review`` run with a
    hand-written reference patch scored targeted 1/1 and broader 1/1, and the
    roll-up had no way to say so.
    """

    def seed_mixed(self):
        self.write_run(
            "r1",
            manifest("r1"),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.SUCCESS.value)]),
        )
        self.write_run(
            "r2",
            manifest("r2"),
            metrics("t2", "bug_fix", [
                dict(attempt("t2::review", "review", Outcome.SUCCESS.value),
                     candidate_source="operator_patch_file")]),
        )
        return summarize_runs(self.runs).to_dict()

    def test_the_rollup_counts_attempts_by_candidate_source(self):
        payload = self.seed_mixed()
        self.assertEqual(
            payload["attempts_by_candidate_source"],
            {"model": 1, "operator_patch_file": 1},
        )
        self.assertEqual(
            payload["aggregate"]["by_candidate_source"],
            {"model": 1, "operator_patch_file": 1},
        )

    def test_an_operator_attempt_adds_a_scope_limit(self):
        payload = self.seed_mixed()
        joined = " ".join(payload["scope_limits"])
        self.assertIn("--patch-file", joined)
        self.assertIn("not about", joined)
        self.assertIn("any model's ability", joined)

    def test_a_model_only_rollup_carries_no_such_limit(self):
        self.seed()
        payload = summarize_runs(self.runs).to_dict()
        self.assertEqual(payload["attempts_by_candidate_source"], {"model": 2})
        self.assertFalse(any("--patch-file" in lim for lim in payload["scope_limits"]))

    def test_the_limit_survives_alongside_the_other_ones(self):
        payload = self.seed_mixed()
        joined = " ".join(payload["scope_limits"])
        self.assertIn("does not establish that a model qualifies", joined)
        self.assertIn("belongs to an authoritative controller", joined)


if __name__ == "__main__":
    unittest.main()


class ReplicationRollupTests(RollupTestCase):
    """A campaign of one run per task is a record, not an average.

    The roll-up already refuses to pool across schemas and flags mixed harness
    revisions. Replication was the quietest gap of all: one run per task per
    endpoint, pooled and totalled, reads like a population when each cell is a
    single observation.
    """

    def test_a_single_attempt_per_task_is_called_out(self):
        self.write_run(
            "r1", manifest("r1"),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.SUCCESS.value)]),
        )
        payload = summarize_runs(self.runs).to_dict()
        joined = " ".join(payload["scope_limits"])
        self.assertIn("contributed a single attempt", joined)
        self.assertIn("no variance is observable", joined)

    def test_the_attempt_counts_per_task_are_recorded(self):
        self.write_run(
            "r1", manifest("r1"),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.SUCCESS.value),
                attempt("t1::single#2", "single", Outcome.SUCCESS.value)]),
        )
        payload = summarize_runs(self.runs).to_dict()
        self.assertEqual(payload["attempts_per_task"], {"t1": 2})
        self.assertFalse(
            any("contributed a single attempt" in lim
                for lim in payload["scope_limits"]),
            "a replicated task must not be described as a singleton",
        )

    def test_the_limit_is_additive(self):
        self.write_run(
            "r1", manifest("r1"),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.SUCCESS.value)]),
        )
        joined = " ".join(summarize_runs(self.runs).to_dict()["scope_limits"])
        self.assertIn("does not establish that a model qualifies", joined)
        self.assertIn("belongs to an authoritative controller", joined)


if __name__ == "__main__":
    unittest.main()


class ReplicationReportingTests(RollupTestCase):
    """A task that passed once out of five is not a task that passed.

    Found by running the corpus against a reasoning model on a GPU: one fixture
    passed at identical settings and then truncated at them. Pass-at-least-once
    is therefore a real category, not a hypothetical, and the roll-up was
    reporting it identically to a clean sweep.

    Previously a task counted in ``successful_tasks`` on a single passing
    attempt, so 1/1 and 1/5 were the same number. For a deterministic model that
    is harmless. For one whose reasoning length decides pass versus truncation,
    it flatters the model by accident.
    """

    def _flaky_run(self):
        self.write_run(
            "r1",
            manifest("r1"),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.SUCCESS.value),
                attempt("t1::single", "single", Outcome.TRUNCATED.value),
                attempt("t1::single", "single", Outcome.SUCCESS.value),
                attempt("t1::single", "single", Outcome.SUCCESS.value),
                attempt("t1::single", "single", Outcome.TRUNCATED.value),
            ]),
        )
        return summarize_runs(self.runs).to_dict()

    def test_a_flaky_task_is_classified_flaky(self):
        record = self._flaky_run()["task_replication"]["t1"]
        self.assertEqual(record["attempts"], 5)
        self.assertEqual(record["successes"], 3)
        self.assertEqual(record["replication"], "flaky")

    def test_flaky_and_reliable_are_counted_separately(self):
        agg = self._flaky_run()["aggregate"]
        family = agg["by_task_family"]["bug_fix"]
        # The original key keeps its meaning: at least one attempt passed.
        self.assertEqual(family["successful_tasks"], 1)
        # And the conflation it invited is now visible rather than implied.
        self.assertEqual(family["tasks_passing_every_attempt"], 0)
        self.assertEqual(family["tasks_flaky"], 1)

    def test_a_flaky_task_is_named_in_a_scope_limit(self):
        joined = " ".join(self._flaky_run()["scope_limits"])
        self.assertIn("passed at least once but not on every attempt", joined)
        self.assertIn("t1 3/5", joined)
        self.assertIn("upper bound", joined)

    def test_a_cleanly_replicated_task_is_reliable_and_quiet(self):
        self.write_run(
            "r1",
            manifest("r1"),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.SUCCESS.value),
                attempt("t1::single", "single", Outcome.SUCCESS.value),
                attempt("t1::single", "single", Outcome.SUCCESS.value),
            ]),
        )
        payload = summarize_runs(self.runs).to_dict()
        self.assertEqual(payload["task_replication"]["t1"]["replication"], "reliable")
        family = payload["aggregate"]["by_task_family"]["bug_fix"]
        self.assertEqual(family["tasks_passing_every_attempt"], 1)
        self.assertEqual(family["tasks_flaky"], 0)
        joined = " ".join(payload["scope_limits"])
        self.assertNotIn("not on every attempt", joined)

    def test_a_task_that_never_passed_is_neither(self):
        self.write_run(
            "r1",
            manifest("r1"),
            metrics("t1", "bug_fix", [
                attempt("t1::single", "single", Outcome.TARGETED_TEST_FAILURE.value),
                attempt("t1::single", "single", Outcome.TARGETED_TEST_FAILURE.value),
            ]),
        )
        payload = summarize_runs(self.runs).to_dict()
        self.assertEqual(payload["task_replication"]["t1"]["replication"], "none")
        family = payload["aggregate"]["by_task_family"]["bug_fix"]
        self.assertEqual(family["successful_tasks"], 0)
        self.assertEqual(family["tasks_flaky"], 0)
        joined = " ".join(payload["scope_limits"])
        self.assertNotIn("not on every attempt", joined)

    def test_it_cannot_be_mistaken_for_a_qualification_verdict(self):
        """The key stays clear of the name ``HonestyTests`` reserves."""
        blob = json.dumps(self._flaky_run())
        for banned in ("pass_rate", "overall_score", "\"verdict\":", "swarm_better"):
            self.assertNotIn(banned, blob, banned)

    def test_a_flaky_task_does_not_inflate_a_replication_claim(self):
        """The point of the whole change, asserted directly.

        A reader computing a success figure from ``successful_tasks`` alone still
        gets 1. What they must not be able to do is mistake that for 1 task that
        reliably works.
        """
        payload = self._flaky_run()
        family = payload["aggregate"]["by_task_family"]["bug_fix"]
        self.assertGreater(family["successful_tasks"], family["tasks_passing_every_attempt"])
        self.assertEqual(payload["task_replication"]["t1"]["successes"], 3)


class BudgetBoundFlakinessTests(RollupTestCase):
    """Why a task flapped decides what to do about it.

    A task that fails only by exhausting its completion budget has not been shown
    to be incapable of the task, and the remedy is a larger ``--max-tokens``. One
    that fails some other way is a different finding. Reporting both as "flaky"
    with no cause throws that away, which is the difference between an actionable
    result and a mysterious one.

    Motivated by a real measurement: a 30B model passed a fixture at
    ``--max-tokens 8192`` and truncated at the same 8192 on another attempt, and
    needed 20,000 before it emitted a diff at all.
    """

    def _flaky(self, budget_bound, extra=None):
        rows = [
            attempt("t1::single", "single", Outcome.SUCCESS.value),
            attempt(
                "t1::single", "single",
                Outcome.TRUNCATED.value,
                budget_bound=budget_bound,
            ),
        ]
        if extra:
            rows.extend(extra)
        self.write_run("r1", manifest("r1"), metrics("t1", "bug_fix", rows))
        return summarize_runs(self.runs).to_dict()

    def test_a_budget_bound_flaky_task_says_so(self):
        record = self._flaky(True)["task_replication"]["t1"]
        self.assertEqual(record["replication"], "flaky")
        self.assertEqual(record["failures"], 1)
        self.assertEqual(record["failures_budget_bound"], 1)
        self.assertEqual(record["failure_cause"], "budget")

    def test_a_capability_flaky_task_is_distinguished(self):
        record = self._flaky(False)["task_replication"]["t1"]
        self.assertEqual(record["replication"], "flaky")
        self.assertEqual(record["failures_budget_bound"], 0)
        self.assertEqual(record["failure_cause"], "capability_or_other")

    def test_mixed_causes_are_reported_as_mixed(self):
        payload = self._flaky(False, extra=[
            attempt("t1::single", "single", Outcome.TRUNCATED.value,
                    budget_bound=True),
        ])
        record = payload["task_replication"]["t1"]
        self.assertEqual(record["failures"], 2)
        self.assertEqual(record["failures_budget_bound"], 1)
        self.assertEqual(record["failure_cause"], "mixed")

    def test_the_scope_limit_names_the_cause_and_the_remedy(self):
        joined = " ".join(self._flaky(True)["scope_limits"])
        self.assertIn("(budget)", joined)
        self.assertIn("larger --max-tokens", joined)

    def test_a_reliable_task_carries_no_cause_claim(self):
        self.write_run("r1", manifest("r1"), metrics("t1", "bug_fix", [
            attempt("t1::single", "single", Outcome.SUCCESS.value),
        ]))
        record = summarize_runs(self.runs).to_dict()["task_replication"]["t1"]
        self.assertNotIn("failure_cause", record)


class AttemptFieldWhitelistTests(RollupTestCase):
    """Nothing in the attempt metrics may vanish between metrics.json and here.

    ``_ATTEMPT_FIELDS`` is a hand-maintained whitelist, and a whitelist drops
    whatever is added to the metrics dataclass and not added to it. ``budget_bound``
    was lost exactly that way while this was being written and nothing failed: the
    field read ``None`` everywhere, which is indistinguishable from an attempt that
    genuinely was not budget-bound.

    The roll-up already has three such hand-maintained field lists -- the best-single
    ranking table, ``_merge_review``, and this one -- and each has dropped something
    while looking fine. So the tuple gets a test rather than a code review.

    Every key in ``AttemptMetrics.to_dict()`` must be one of three things: a
    whitelisted field carried verbatim, a derived key, or explicitly not carried.
    The classification is therefore auditable rather than implied.
    """

    def test_every_metrics_field_is_accounted_for(self):
        from realtask.metrics import AttemptMetrics
        from realtask.summarize import (
            _ATTEMPT_FIELDS,
            _DERIVED_ROW_KEYS,
            _NOT_CARRIED_PER_ATTEMPT,
        )
        from realtask.taxonomy import Outcome

        blank = AttemptMetrics(
            attempt_id="a", strategy="single", outcome=Outcome.SUCCESS
        ).to_dict()
        unaccounted = sorted(
            set(blank) - set(_ATTEMPT_FIELDS) - _DERIVED_ROW_KEYS
            - _NOT_CARRIED_PER_ATTEMPT
        )
        self.assertEqual(
            unaccounted, [],
            "these metrics fields are neither carried, derived, nor declared "
            "not-carried, so they read as None in every roll-up: {}".format(
                unaccounted
            ),
        )

    def test_budget_bound_reaches_the_rollup_row_end_to_end(self):
        """The specific loss that motivated this, asserted through the real path."""
        self.write_run("r1", manifest("r1"), metrics("t1", "bug_fix", [
            attempt("t1::single", "single", Outcome.SUCCESS.value),
            attempt("t1::single", "single", Outcome.TRUNCATED.value,
                    budget_bound=True),
        ]))
        rows = summarize_runs(self.runs).to_dict()["tasks"][0]["attempts"]
        by_id = {r["attempt_id"] + r["outcome"]: r for r in rows}
        truncated = [r for r in rows if r["outcome"] == Outcome.TRUNCATED.value]
        self.assertTrue(truncated)
        self.assertIs(
            truncated[0]["budget_bound"], True,
            "budget_bound was dropped by the whitelist, so every budget-bound "
            "attempt would read as None and look like an ordinary truncation",
        )
