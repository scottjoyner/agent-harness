"""Evidence-format tests: run layout, atomic writes, provenance, comparison.

Two obligations are checked here. First, that a run directory is a complete,
self-describing, durably written record. Second, that evidence written by this
harness can never be mistaken for the historical benchmark artifacts already
committed to this repository.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from test_realtask_support import (
    AUTO_INGEST_BUG_FIX,
    AUTO_INGEST_CONTRACT,
    REPO_ROOT,
    HarnessTestCase,
    analysis_reply,
    patch_reply,
    review_reply,
    scout_reply,
)

from realtask.compare import (
    COST_COMPONENTS,
    QUALITY_COMPONENTS,
    build_comparison,
)
from realtask.metrics import AttemptMetrics
from realtask.evidence import (
    RunDirectory,
    atomic_write_json,
    harness_provenance,
    source_manifest_artifact,
    utc_now,
)
from realtask.taxonomy import Outcome
from realtask.version import (
    SCHEMA_COMPARISON,
    SCHEMA_METRICS,
    SCHEMA_ROLE_RESULT,
    SCHEMA_RUN_MANIFEST,
    SCHEMA_SOURCE_MANIFEST,
    SCHEMA_TASK,
    SCHEMA_TEST_RESULTS,
)

LEGACY_ARTIFACT_GLOBS = (
    "bench_*.json",
    "bench_v*_*.json",
    "benchmark_*.json",
    "dual_*.json",
    "cpm_tb2_*.json",
)


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="realtask-evidence-")
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_write_is_durable_and_parseable(self):
        target = self.tmp / "nested" / "payload.json"
        atomic_write_json(target, {"b": 2, "a": 1})
        self.assertTrue(target.is_file())
        self.assertEqual(json.loads(target.read_text()), {"a": 1, "b": 2})

    def test_no_temporary_files_are_left_behind(self):
        target = self.tmp / "payload.json"
        atomic_write_json(target, {"x": 1})
        leftovers = [p.name for p in self.tmp.iterdir() if p.name != target.name]
        self.assertEqual(leftovers, [])

    def test_overwrite_never_leaves_a_partial_file(self):
        target = self.tmp / "payload.json"
        atomic_write_json(target, {"generation": 1})
        first = target.read_text()
        atomic_write_json(target, {"generation": 2, "payload": "x" * 100000})
        second = target.read_text()
        self.assertEqual(json.loads(first)["generation"], 1)
        self.assertEqual(json.loads(second)["generation"], 2)

    def test_failed_serialisation_leaves_the_previous_file_intact(self):
        target = self.tmp / "payload.json"
        atomic_write_json(target, {"generation": 1})
        with self.assertRaises(TypeError):
            atomic_write_json(target, {"bad": object()})
        self.assertEqual(json.loads(target.read_text()), {"generation": 1})
        leftovers = [p.name for p in self.tmp.iterdir() if p.name != target.name]
        self.assertEqual(leftovers, [])


class RunDirectoryTests(HarnessTestCase):
    def test_role_directories_are_precreated(self):
        for role in ("single", "scout", "implementer", "reviewer", "swarm"):
            self.assertTrue((self.run_dir.path / role).is_dir(), role)

    def test_unknown_role_directory_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_dir.role_dir("router")

    def test_manifest_round_trip_and_finalisation(self):
        self.run_dir.write_run_manifest(
            harness_sha="deadbeef",
            harness_dirty=False,
            fixture_sha256="cafe",
            task_id="t",
            task_family="bug_fix",
            endpoints=[{"label": "x", "model": "m"}],
            strategies=["single"],
            argv=["realtime_bench.py"],
            host={"hostname": "h"},
            options={},
            started_at=utc_now(),
        )
        payload = json.loads((self.run_dir.path / "manifest.json").read_text())
        self.assertEqual(payload["schema"], SCHEMA_RUN_MANIFEST)
        self.assertEqual(payload["harness"]["git_sha"], "deadbeef")
        self.assertFalse(payload["authority"]["authoritative_repo_mutated"])
        self.assertFalse(payload["authority"]["assistx_task_state_mutated"])
        self.assertFalse(payload["authority"]["routing_or_admission_mutated"])

        self.run_dir.write_json("metrics.json", {"schema": SCHEMA_METRICS})
        self.run_dir.finalize_manifest({"integrity": {"authoritative_source_unchanged": True}})
        final = json.loads((self.run_dir.path / "manifest.json").read_text())
        self.assertIn("metrics.json", final["artifacts_written"])
        self.assertIn("manifest.json", final["artifacts_written"])
        self.assertTrue(final["integrity"]["authoritative_source_unchanged"])

    def test_api_key_is_never_recorded(self):
        from realtask.adapter import EndpointConfig

        config = EndpointConfig(
            label="node", base_url="http://host:1234/v1", model="m", api_key="sk-secret-value"
        )
        identity = config.identity()
        self.assertNotIn("api_key", identity)
        self.assertTrue(identity["api_key_set"])
        self.assertNotIn("sk-secret-value", json.dumps(identity))
        self.assertIsNone(config.redacted().api_key)

    def test_endpoint_identity_is_not_a_hardcoded_fleet(self):
        from realtask.adapter import EndpointConfig

        package = "\n".join(
            p.read_text() for p in (REPO_ROOT / "realtask").glob("*.py")
        )
        for node in ("OptiPlex", "Lenovo", "Destroyer", "100.64.43.123"):
            self.assertNotIn(node, package, "harness hardcodes node {!r}".format(node))
        config = EndpointConfig(label="whatever", base_url="http://x/v1", model="m")
        self.assertEqual(config.identity()["label"], "whatever")

    def test_harness_provenance_reports_the_repo_sha(self):
        provenance = harness_provenance(REPO_ROOT)
        self.assertEqual(len(provenance["git_sha"]), 40)
        self.assertIn("git_dirty", provenance)


class EvidenceContentTests(HarnessTestCase):
    def test_single_run_writes_the_documented_artifacts(self):
        _task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        self.assertOutcome(result.attempts[0], Outcome.SUCCESS)
        for name in (
            "task.json",
            "metrics.json",
            "test-results.json",
            "patch.diff",
            "single/result.json",
            "single/metrics.json",
            "single/single.txt",
            "single/test-evidence.txt",
        ):
            self.assertTrue((self.run_dir.path / name).is_file(), name)

        metrics = json.loads((self.run_dir.path / "metrics.json").read_text())
        self.assertEqual(metrics["schema"], SCHEMA_METRICS)
        self.assertEqual(metrics["fixture_sha256"], result.task_metrics.fixture_sha256)
        self.assertEqual(metrics["source_head"], result.task_metrics.source_head)
        self.assertEqual(metrics["attempts"][0]["outcome"], "SUCCESS")

        payload = json.loads((self.run_dir.path / "single" / "result.json").read_text())
        self.assertEqual(payload["schema"], SCHEMA_ROLE_RESULT)
        self.assertIn("binding", payload)

        tests = json.loads((self.run_dir.path / "test-results.json").read_text())
        self.assertEqual(tests["schema"], SCHEMA_TEST_RESULTS)
        self.assertEqual(tests["attempts"][0]["tests"]["targeted_total"], 1)
        self.assertTrue(tests["attempts"][0]["patch_applied"])

    def test_patch_diff_holds_the_verbatim_candidate(self):
        from test_realtask_support import REFERENCE_REPAIR

        self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        body = (self.run_dir.path / "patch.diff").read_text()
        for line in REFERENCE_REPAIR.strip().splitlines():
            self.assertIn(line, body)

    def test_swarm_run_writes_role_evidence(self):
        _task, result = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(), review_reply("accept")],
            ["swarm"],
        )
        self.assertOutcome(result.attempts[0], Outcome.SUCCESS)
        for name in (
            "swarm/result.json",
            "swarm/metrics.json",
            "swarm/scout.txt",
            "swarm/implementer.txt",
            "swarm/reviewer.txt",
            "swarm/test-evidence.txt",
            "patch.diff",
            "test-results.json",
            "metrics.json",
        ):
            self.assertTrue((self.run_dir.path / name).is_file(), name)
        payload = json.loads((self.run_dir.path / "swarm" / "result.json").read_text())
        self.assertIn("scout", payload)
        self.assertIn("implementer", payload)
        self.assertIn("reviewer", payload)

    def test_analysis_run_records_no_patch(self):
        self.run_stages(AUTO_INGEST_CONTRACT, [analysis_reply()], ["single"])
        payload = json.loads((self.run_dir.path / "single" / "result.json").read_text())
        self.assertNotIn("implementer", payload)
        self.assertIn("scout", payload)
        self.assertFalse((self.run_dir.path / "patch.diff").exists())

    def test_token_metrics_stay_null_when_the_endpoint_omits_usage(self):
        from realtask.adapter import ChatResponse

        self.assertIsNone(ChatResponse(
            role="single", content="{}", finish_reason="stop",
            prompt_tokens=None, completion_tokens=None, total_tokens=None,
            usage_reported=False, wall_s=1.0, ttft_s=None, tokens_per_s=None,
            prompt_tokens_per_s=None,
        ).total_tokens)

    def test_metrics_keep_components_separate(self):
        _task, result = self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        metrics = result.attempts[0].metrics.to_dict()
        for key in ("grounding", "patch", "tests", "review", "calls"):
            self.assertIn(key, metrics)
        self.assertNotIn("score", {k for k in metrics if k == "quality_score"})
        self.assertIn("score", metrics["grounding"])
        self.assertNotIn("score", metrics["patch"])


class ComparisonTests(HarnessTestCase):
    def single_and_swarm(self):
        _t, single = self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        _t2, swarm = self.run_stages(
            AUTO_INGEST_BUG_FIX,
            [scout_reply(), patch_reply(), review_reply("accept")],
            ["swarm"],
        )
        return single, swarm

    def test_comparison_retains_every_component_metric(self):
        single, swarm = self.single_and_swarm()
        comparison = build_comparison(
            "t", "bug_fix", "fixture-sha",
            [single.attempts[0].metrics], swarm.attempts[0].metrics,
            integration_overhead_s=1.5,
        )
        self.assertEqual(comparison["schema"], SCHEMA_COMPARISON)
        for component in QUALITY_COMPONENTS:
            self.assertIn(component, comparison["quality_difference"]["components"], component)
        for component in COST_COMPONENTS:
            self.assertIn(component, comparison["cost_difference"], component)

    def test_comparison_has_no_composite_score(self):
        single, swarm = self.single_and_swarm()
        comparison = build_comparison(
            "t", "bug_fix", "f", [single.attempts[0].metrics], swarm.attempts[0].metrics
        )
        self.assertIsNone(comparison["quality_difference"]["composite_score"])
        blob = json.dumps(comparison)
        self.assertNotIn("overall_score", blob)
        self.assertNotIn("swarm_better", blob)

    def test_comparison_reports_cost_and_call_deltas(self):
        single, swarm = self.single_and_swarm()
        comparison = build_comparison(
            "t", "bug_fix", "f", [single.attempts[0].metrics], swarm.attempts[0].metrics
        )
        calls = comparison["cost_difference"]["model_calls"]
        self.assertEqual(calls["single"], 1)
        self.assertEqual(calls["swarm"], 3)
        self.assertEqual(calls["delta"], 2.0)
        self.assertEqual(comparison["cost_difference"]["roles"]["single"], ["single"])
        self.assertEqual(
            comparison["cost_difference"]["roles"]["swarm"], ["scout", "implementer", "reviewer"]
        )

    def test_comparison_states_its_scope_limits(self):
        single, swarm = self.single_and_swarm()
        comparison = build_comparison(
            "t", "bug_fix", "f", [single.attempts[0].metrics], swarm.attempts[0].metrics
        )
        joined = " ".join(comparison["scope_limits"])
        self.assertIn("One task is not evidence", joined)
        self.assertIn("No component is aggregated", joined)
        self.assertIn("evidence only", joined)

    def test_best_single_selection_is_explicit_and_auditable(self):
        _t, single = self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        from test_realtask_support import NON_APPLYING_PATCH

        _t2, broken = self.run_stages(
            AUTO_INGEST_BUG_FIX, [patch_reply(NON_APPLYING_PATCH)], ["single"]
        )
        comparison = build_comparison(
            "t", "bug_fix", "f",
            [single.attempts[0].metrics, broken.attempts[0].metrics], None,
        )
        self.assertEqual(len(comparison["single_attempts_considered"]), 2)
        self.assertIn("failure-rank", comparison["best_single_selection_rule"])
        self.assertEqual(comparison["best_single"]["quality"]["outcome"], "SUCCESS")

    def test_the_best_single_ranking_covers_every_outcome(self):
        """A new Outcome must not be ranked worst without anyone noticing.

        ``_select_best_single`` falls back to rank 99 for any outcome missing
        from its table. That is a safe default only while the table is complete,
        and completeness is not a property the type system enforces: add a
        member and it silently sorts worse than every real outcome, so the
        comparison artifact can name the wrong attempt as best. Nothing else
        would go red.
        """
        import inspect
        import re

        from realtask.compare import _select_best_single
        from realtask.taxonomy import Outcome

        source = inspect.getsource(_select_best_single)
        ranked = re.findall(r"Outcome\.([A-Z_]+):\s*(\d+)", source)
        names = [name for name, _rank in ranked]
        ranks = [int(rank) for _name, rank in ranked]

        self.assertEqual(
            sorted(names), sorted({o.name for o in Outcome}),
            "the best-single ranking and the Outcome enum have diverged",
        )
        self.assertEqual(
            len(names), len(set(names)),
            "an outcome is ranked twice: {}".format(
                sorted({n for n in names if names.count(n) > 1})
            ),
        )
        self.assertEqual(
            sorted(ranks), list(range(len(ranks))),
            "ranks must be contiguous from 0 so no outcome ties with the "
            "fallback: {}".format(sorted(ranks)),
        )

    def test_the_ranking_order_that_is_a_judgement_call_is_pinned(self):
        """Some orderings are decisions, so changing one must be deliberate."""
        from realtask.compare import _select_best_single
        from realtask.taxonomy import Outcome

        def best_of(better, worse):
            def attempt(outcome, attempt_id):
                return AttemptMetrics(
                    attempt_id=attempt_id, strategy="single", outcome=outcome
                )

            chosen, _rule = _select_best_single(
                [attempt(worse, "worse"), attempt(better, "better")]
            )
            return chosen.attempt_id == "better"

        # SUCCESS is the best outcome, full stop.
        for other in Outcome:
            if other is Outcome.SUCCESS:
                continue
            with self.subTest(against=other.name):
                self.assertTrue(best_of(Outcome.SUCCESS, other))

        # A fixture that would not bind is worse evidence than a hang: the
        # attempt never measured anything.
        self.assertTrue(best_of(Outcome.TIMEOUT, Outcome.SOURCE_MISMATCH))
        # A harness bug is worse than a wrong answer.
        self.assertTrue(best_of(Outcome.TARGETED_TEST_FAILURE,
                                Outcome.PROTOCOL_FAILURE))

    def test_the_comparison_names_where_each_candidate_came_from(self):
        """An operator's patch must not read as a model's result."""
        from realtask.metrics import CANDIDATE_SOURCE_OPERATOR

        operator = AttemptMetrics(
            attempt_id="t::review", strategy="review", outcome=Outcome.SUCCESS
        )
        operator.candidate_source = CANDIDATE_SOURCE_OPERATOR
        comparison = build_comparison("t", "bug_fix", "f", [operator], None)

        self.assertEqual(
            comparison["single_attempts_considered"][0]["candidate_source"],
            "operator_patch_file",
        )
        joined = " ".join(comparison["scope_limits"])
        self.assertIn("--patch-file", joined)
        self.assertIn("must not be read as a model result", joined)

    def test_a_model_only_comparison_carries_no_such_limit(self):
        model = AttemptMetrics(
            attempt_id="t::single", strategy="single", outcome=Outcome.SUCCESS
        )
        comparison = build_comparison("t", "bug_fix", "f", [model], None)
        self.assertEqual(
            comparison["single_attempts_considered"][0]["candidate_source"], "model"
        )
        self.assertFalse(any("--patch-file" in l for l in comparison["scope_limits"]))

    def test_the_swarm_side_origin_is_reported_too(self):
        from realtask.metrics import CANDIDATE_SOURCE_OPERATOR

        model = AttemptMetrics(
            attempt_id="t::single", strategy="single", outcome=Outcome.SUCCESS
        )
        swarm = AttemptMetrics(
            attempt_id="t::swarm", strategy="swarm", outcome=Outcome.SUCCESS
        )
        swarm.candidate_source = CANDIDATE_SOURCE_OPERATOR
        comparison = build_comparison("t", "bug_fix", "f", [model], swarm)
        self.assertEqual(comparison["swarm_candidate_source"], "operator_patch_file")
        self.assertIn("t::swarm", " ".join(comparison["scope_limits"]))

    def test_comparison_handles_a_missing_side(self):
        _t, single = self.run_stages(AUTO_INGEST_BUG_FIX, [patch_reply()], ["single"])
        _t2, swarm = self.single_and_swarm()

        without_swarm = build_comparison(
            "t", "bug_fix", "f", [single.attempts[0].metrics], None
        )
        self.assertIsNone(without_swarm["swarm"])
        self.assertEqual(without_swarm["quality_difference"], {})
        self.assertIsNotNone(without_swarm["best_single"])

        without_single = build_comparison(
            "t", "bug_fix", "f", [], swarm.attempts[0].metrics
        )
        self.assertIsNone(without_single["best_single"])
        self.assertEqual(
            without_single["best_single_selection_rule"],
            "no single attempt was available to compare",
        )
        self.assertEqual(without_single["cost_difference"], {})

    def test_reviewer_contribution_is_reported_without_inflation(self):
        single, swarm = self.single_and_swarm()
        comparison = build_comparison(
            "t", "bug_fix", "f", [single.attempts[0].metrics], swarm.attempts[0].metrics
        )
        contribution = comparison["reviewer_contribution"]
        self.assertTrue(contribution["review_ran"])
        self.assertFalse(contribution["refinement_issued"])
        self.assertEqual(contribution["refinement_budget"], 1)
        self.assertIn("not, by itself, evidence", contribution["interpretation_note"])

    def test_missing_token_counts_are_not_imputed(self):
        single, swarm = self.single_and_swarm()
        for attempt in (single.attempts[0], swarm.attempts[0]):
            for call in attempt.metrics.calls:
                call.total_tokens = None
        comparison = build_comparison(
            "t", "bug_fix", "f", [single.attempts[0].metrics], swarm.attempts[0].metrics
        )
        tokens = comparison["cost_difference"]["total_tokens"]
        self.assertIsNone(tokens["single"])
        self.assertIsNone(tokens["swarm"])
        self.assertIsNone(tokens["delta"])


class ProvenanceSeparationTests(unittest.TestCase):
    def test_legacy_artifacts_exist_and_are_not_reinterpreted(self):
        found = []
        for pattern in LEGACY_ARTIFACT_GLOBS:
            found.extend(REPO_ROOT.glob(pattern))
        self.assertTrue(found, "expected historical benchmark artifacts in the repo")

        registry = json.loads((REPO_ROOT / "EVIDENCE-PROVENANCE.json").read_text())
        legacy_paths = {entry["path"] for entry in registry["legacy_artifacts"]}
        for path in found:
            if path.name == "benchmarks" or path.is_dir():
                continue
            with self.subTest(artifact=path.name):
                self.assertIn(
                    path.name, legacy_paths,
                    "{} is not registered as a legacy artifact".format(path.name),
                )

    def test_registry_declares_no_equivalence_with_the_new_schema(self):
        registry = json.loads((REPO_ROOT / "EVIDENCE-PROVENANCE.json").read_text())
        self.assertEqual(registry["current_schema_family"], "realtask.*.v1")
        for entry in registry["legacy_artifacts"]:
            self.assertNotEqual(entry.get("schema"), "realtask.metrics.v1")
            self.assertIn(entry["status"], {"legacy", "superseded"})
            self.assertFalse(entry.get("equivalent_to_realtask", False))

    def test_new_artifacts_carry_the_realtask_schema_marker(self):
        for schema in (
            SCHEMA_RUN_MANIFEST, SCHEMA_TASK, SCHEMA_SOURCE_MANIFEST,
            SCHEMA_ROLE_RESULT, SCHEMA_TEST_RESULTS, SCHEMA_METRICS, SCHEMA_COMPARISON,
        ):
            self.assertTrue(schema.startswith("realtask."), schema)
            self.assertTrue(schema.endswith(".v1"), schema)

    def test_no_legacy_artifact_carries_a_realtask_schema(self):
        registry = json.loads((REPO_ROOT / "EVIDENCE-PROVENANCE.json").read_text())
        for entry in registry["legacy_artifacts"]:
            path = REPO_ROOT / entry["path"]
            if not path.is_file():
                continue
            text = path.read_text(errors="replace")
            with self.subTest(artifact=entry["path"]):
                self.assertNotIn("realtask.", text)

    def test_registry_documents_every_historical_bench_script(self):
        registry = json.loads((REPO_ROOT / "EVIDENCE-PROVENANCE.json").read_text())
        registered = {entry["path"] for entry in registry["legacy_artifacts"]}
        for script in (
            "benchmark.py", "quick_benchmark.py", "fast_bench.py", "fast_bench_v2.py",
            "fast_bench_v3.py", "fast_bench_v4.py", "fast_bench_v5.py",
            "fast_bench_v6.py", "fast_bench_v7.py", "baremetal_bench.py",
            "terminal_bench_harness.py", "toolcall_harness.py", "dual_model_harness.py",
            "synergistic_harness.py", "native_toolcall_bench.py", "cpm_tb2_bench.py",
            "cpm_tb_agent.py", "router.py", "run_dual_4.py", "run_terminal_bench.py",
            "simple_dual.py", "fixgit_repro_v1.py",
        ):
            with self.subTest(script=script):
                self.assertTrue((REPO_ROOT / script).is_file(), script)
                self.assertIn(script, registered, "{} is unregistered".format(script))

    def test_canonical_runner_is_declared(self):
        registry = json.loads((REPO_ROOT / "EVIDENCE-PROVENANCE.json").read_text())
        self.assertEqual(registry["canonical_real_task_entrypoint"], "realtime_bench.py")
        self.assertTrue((REPO_ROOT / registry["canonical_real_task_entrypoint"]).is_file())


class SourceManifestArtifactTests(HarnessTestCase):
    def test_artifact_carries_the_frozen_manifest_and_the_verdict(self):
        from realtask.binding import verify_source_binding
        from realtask.fixtures import load_source_manifest

        task = self.task(AUTO_INGEST_BUG_FIX)
        binding = verify_source_binding(task)
        artifact = source_manifest_artifact(
            load_source_manifest(task), binding, {"note": "ok"}
        )
        self.assertEqual(artifact["schema"], SCHEMA_SOURCE_MANIFEST)
        self.assertEqual(
            artifact["manifest"]["files"][0]["sha256"], task.source.files[0].sha256
        )
        self.assertTrue(artifact["binding"]["ok"])
        self.assertIn("source_binding_sha256", artifact["binding"])


if __name__ == "__main__":
    unittest.main()
