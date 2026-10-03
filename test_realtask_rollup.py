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


if __name__ == "__main__":
    unittest.main()
