"""Frozen RealTask fixture and source-binding tests.

The binding tests are the fail-closed guarantee: wrong bytes, wrong clone, or
wrong HEAD must stop the benchmark before any model is called.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_realtask_support import (
    AUTO_INGEST_BUG_FIX,
    AUTO_INGEST_CONTRACT,
    AUTO_INGEST_TEST_GEN,
    REPO_ROOT,
    TASKS_ROOT,
    HarnessTestCase,
)

import seal_realtask_fixture
from realtask.adapter import ScriptedResponse
from realtask.binding import (
    HEAD_RECORDED,
    HEAD_VERIFIED,
    MODE_EXTERNAL,
    MODE_SNAPSHOT,
    git_head,
    verify_source_binding,
)
from realtask.fixtures import (
    TASK_FAMILIES,
    FixtureError,
    RealTask,
    iter_fixture_manifests,
    load_source_manifest,
    load_task,
    load_task_by_id,
)
from realtask.taxonomy import Outcome
from realtask.version import SCHEMA_SOURCE_MANIFEST, SCHEMA_TASK

SECRET_MARKERS = ("password", "api_key", "api-key", "secret", "token", "bolt://", "bearer ")


class FixtureCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="realtask-fixture-")
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def copy_fixture(self, task_id: str, name: str = "fixture") -> Path:
        dest = self.tmp / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(TASKS_ROOT / task_id, dest)
        return dest

    def rewrite_task(self, root: Path, mutate) -> None:
        payload = json.loads((root / "task.json").read_text())
        mutate(payload)
        (root / "task.json").write_text(json.dumps(payload, indent=2, sort_keys=True))

    def test_every_required_family_is_represented(self):
        families = {load_task(m).task_family for m in iter_fixture_manifests(TASKS_ROOT)}
        for family in TASK_FAMILIES:
            self.assertIn(family, families, "no fixture declares family {!r}".format(family))

    def test_every_fixture_loads_and_self_verifies(self):
        manifests = iter_fixture_manifests(TASKS_ROOT)
        self.assertTrue(manifests)
        for manifest in manifests:
            task = load_task(manifest)
            with self.subTest(task=task.task_id):
                payload = load_source_manifest(task)
                self.assertEqual(payload["schema"], SCHEMA_SOURCE_MANIFEST)
                binding = verify_source_binding(task)
                self.assertTrue(binding.ok, binding.reasons)
                self.assertEqual(binding.head_status, HEAD_RECORDED)
                self.assertTrue(task.acceptance.targeted, "no acceptance commands")

    def test_fixture_identity_changes_with_content(self):
        task = load_task_by_id(AUTO_INGEST_BUG_FIX, TASKS_ROOT)
        before = task.fixture_sha256()
        before_snapshot = task.snapshot_sha256()
        target = task.source_dir / "auto_ingest" / "shorts" / "cli.py"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# drift\n")
            drifted = load_task_by_id(AUTO_INGEST_BUG_FIX, TASKS_ROOT)
            self.assertNotEqual(drifted.snapshot_sha256(), before_snapshot)
            self.assertNotEqual(drifted.fixture_sha256(), before)
        finally:
            target.write_bytes(original)
        self.assertEqual(
            load_task_by_id(AUTO_INGEST_BUG_FIX, TASKS_ROOT).fixture_sha256(), before
        )

    def test_unknown_key_is_rejected(self):
        root = self.copy_fixture(AUTO_INGEST_CONTRACT)
        self.rewrite_task(root, lambda p: p.update({"typo_field": "should fail closed"}))
        with self.assertRaises(FixtureError) as ctx:
            load_task(root / "task.json")
        self.assertIn("typo_field", str(ctx.exception))

    def test_unknown_family_is_rejected(self):
        root = self.copy_fixture(AUTO_INGEST_CONTRACT)
        self.rewrite_task(root, lambda p: p.update({"task_family": "synthetic_puzzle"}))
        with self.assertRaises(FixtureError) as ctx:
            load_task(root / "task.json")
        self.assertIn("synthetic_puzzle", str(ctx.exception))

    def test_unknown_deliverable_is_rejected(self):
        root = self.copy_fixture(AUTO_INGEST_CONTRACT)
        self.rewrite_task(root, lambda p: p.update({"deliverable": "shell_commands"}))
        with self.assertRaises(FixtureError) as ctx:
            load_task(root / "task.json")
        self.assertIn("shell_commands", str(ctx.exception))

    def test_fixture_without_acceptance_commands_is_rejected(self):
        root = self.copy_fixture(AUTO_INGEST_CONTRACT)
        self.rewrite_task(root, lambda p: p["acceptance"].update({"targeted": []}))
        with self.assertRaises(FixtureError) as ctx:
            load_task(root / "task.json")
        self.assertIn("acceptance.targeted", str(ctx.exception))

    def test_relevant_files_must_exist_in_the_source_set(self):
        root = self.copy_fixture(AUTO_INGEST_CONTRACT)
        self.rewrite_task(
            root, lambda p: p.update({"relevant_source_files": ["auto_ingest/shorts/ghost.py"]})
        )
        with self.assertRaises(FixtureError) as ctx:
            load_task(root / "task.json")
        self.assertIn("ghost.py", str(ctx.exception))

    def test_absolute_paths_are_rejected(self):
        root = self.copy_fixture(AUTO_INGEST_CONTRACT)
        self.rewrite_task(
            root, lambda p: p["source"]["files"][0].update({"path": "/etc/passwd"})
        )
        with self.assertRaises(FixtureError):
            load_task(root / "task.json")

    def test_seal_check_detects_drift(self):
        import contextlib
        import io

        self.assertEqual(
            seal_realtask_fixture.main([str(TASKS_ROOT / AUTO_INGEST_BUG_FIX), "--check"]), 0
        )
        root = self.copy_fixture(AUTO_INGEST_BUG_FIX, "drifted")
        (root / "source" / "auto_ingest" / "shorts" / "cli.py").write_text("# drift\n")
        noise = io.StringIO()
        with contextlib.redirect_stderr(noise):
            self.assertEqual(seal_realtask_fixture.main([str(root), "--check"]), 1)
        self.assertIn("DRIFT", noise.getvalue())

    def test_fixtures_bundle_no_secrets(self):
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            raw = manifest.read_text().lower()
            with self.subTest(task=manifest.parent.name):
                for marker in SECRET_MARKERS:
                    self.assertNotIn(marker, raw, "fixture manifest leaks {!r}".format(marker))

    def test_campaign_fixture_records_real_provenance(self):
        task = load_task_by_id(AUTO_INGEST_BUG_FIX, TASKS_ROOT)
        self.assertEqual(task.source.repository, "scottjoyner/auto-ingest")
        self.assertEqual(len(task.source.head), 40)
        self.assertIn("auto_ingest/shorts/cli.py", task.relevant_source_files)
        self.assertIsNotNone(task.known_regression)
        self.assertIn("planner.plan_shorts(..., driver=driver)", task.problem)
        self.assertIn("closed driver", task.known_regression)


class SourceBindingTests(HarnessTestCase):
    def make_clone(self, task, dest=None, commit_message="snapshot"):
        dest = dest or (self.tmp / "clone")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(task.source_dir, dest)
        self.git("init", "-q", "-b", "master", str(dest))
        self.git("-C", str(dest), "add", "-A")
        self.git("-C", str(dest), "commit", "-q", "-m", commit_message)
        return dest

    def variant_fixture(self, task_id: str, head: str) -> RealTask:
        """A byte-identical fixture whose declared HEAD is ``head``.

        Used to prove that a matching HEAD *and* matching hashes is a legitimate
        binding, so the mismatch tests are not passing for the wrong reason.
        """
        root = self.tmp / "variant-{}".format(task_id)
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(TASKS_ROOT / task_id, root)
        payload = json.loads((root / "task.json").read_text())
        payload["source"]["head"] = head
        (root / "task.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
        self.seal_quietly(root)
        return load_task(root / "task.json")

    def test_snapshot_mode_is_the_default(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        binding = verify_source_binding(task)
        self.assertTrue(binding.ok)
        self.assertEqual(binding.mode, MODE_SNAPSHOT)
        self.assertEqual(binding.source_root, str(task.source_dir))

    def test_wrong_file_hash_fails_closed(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        root = self.tmp / "tampered"
        shutil.copytree(task.source_dir, root)
        (root / "auto_ingest" / "shorts" / "cli.py").write_text("# tampered\n")
        binding = verify_source_binding(task, source_root=root)
        self.assertFalse(binding.ok)
        self.assertIs(binding.outcome, Outcome.SOURCE_MISMATCH)
        self.assertEqual(binding.mode, MODE_EXTERNAL)
        self.assertTrue(any("hash mismatch" in reason for reason in binding.reasons))

    def test_missing_file_fails_closed(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        root = self.tmp / "incomplete"
        shutil.copytree(task.source_dir, root)
        (root / "auto_ingest" / "shorts" / "planner.py").unlink()
        binding = verify_source_binding(task, source_root=root)
        self.assertFalse(binding.ok)
        self.assertIs(binding.outcome, Outcome.SOURCE_MISMATCH)
        self.assertTrue(any("absent" in reason for reason in binding.reasons))

    def test_wrong_source_sha_fails_closed(self):
        """The headline guarantee: another clone is never benchmarked silently."""
        task = self.task(AUTO_INGEST_BUG_FIX)
        clone = self.make_clone(task)
        other_head = self.git("rev-parse", "HEAD", cwd=clone).stdout.strip()
        self.assertNotEqual(other_head, task.source.head)

        binding = verify_source_binding(task, source_root=clone)
        self.assertFalse(binding.ok)
        self.assertIs(binding.outcome, Outcome.SOURCE_MISMATCH)
        self.assertEqual(binding.head_status, HEAD_VERIFIED)
        self.assertEqual(binding.actual_head, other_head)
        self.assertTrue(any("HEAD mismatch" in reason for reason in binding.reasons))

    def test_non_git_source_root_cannot_verify_head(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        root = self.tmp / "plain"
        shutil.copytree(task.source_dir, root)
        binding = verify_source_binding(task, source_root=root)
        self.assertFalse(binding.ok)
        self.assertIs(binding.outcome, Outcome.SOURCE_MISMATCH)
        self.assertTrue(
            any("not itself a git repository root" in reason
                for reason in binding.reasons),
            list(binding.reasons),
        )

    def test_a_directory_nested_in_another_checkout_cannot_borrow_its_head(self):
        """A HEAD belonging to an enclosing repository is not provenance.

        ``git rev-parse`` walks up parent directories, so a source root sitting
        inside some unrelated checkout would otherwise report that checkout's
        HEAD and appear to bind successfully.
        """
        task = self.task(AUTO_INGEST_BUG_FIX)
        outer = self.tmp / "outer-repo"
        outer.mkdir()
        self.git("init", "-q", "-b", "master", str(outer))
        inner = outer / "some" / "nested" / "path"
        shutil.copytree(task.source_dir, inner)

        self.assertEqual(
            git_head(inner),
            None,
            "a nested directory must not inherit the enclosing repository's HEAD",
        )
        binding = verify_source_binding(task, source_root=inner)
        self.assertFalse(binding.ok)
        self.assertIs(binding.outcome, Outcome.SOURCE_MISMATCH)
        self.assertIsNone(binding.actual_head)

    def test_a_real_repository_root_still_binds(self):
        """The hardening must not break legitimate external-worktree binding."""
        task = self.task(AUTO_INGEST_BUG_FIX)
        clone = self.make_clone(task)
        real_head = self.git("rev-parse", "HEAD", cwd=clone).stdout.strip()
        variant = self.variant_fixture(AUTO_INGEST_BUG_FIX, real_head)
        self.assertEqual(git_head(clone), real_head)
        self.assertTrue(verify_source_binding(variant, source_root=clone).ok)

    def test_snapshot_mode_never_claims_a_verified_head(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        binding = verify_source_binding(task)
        self.assertEqual(binding.head_status, HEAD_RECORDED)
        self.assertIsNone(binding.actual_head)

    def test_matching_head_and_hashes_pass(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        clone = self.make_clone(task)
        real_head = self.git("rev-parse", "HEAD", cwd=clone).stdout.strip()
        variant = self.variant_fixture(AUTO_INGEST_BUG_FIX, real_head)

        self.assertFalse(
            verify_source_binding(task, source_root=clone).ok,
            "the unvaried fixture must still fail against a different HEAD",
        )
        binding = verify_source_binding(variant, source_root=clone)
        self.assertTrue(binding.ok, binding.reasons)
        self.assertEqual(binding.actual_head, real_head)
        self.assertEqual(binding.head_status, HEAD_VERIFIED)

    def test_source_binding_sha_is_stable_and_content_sensitive(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        first = verify_source_binding(task).source_binding_sha256
        self.assertEqual(first, verify_source_binding(task).source_binding_sha256)
        root = self.tmp / "mutated"
        shutil.copytree(task.source_dir, root)
        (root / "auto_ingest" / "shorts" / "cli.py").write_text("# different\n")
        self.assertNotEqual(first, verify_source_binding(task, source_root=root).source_binding_sha256)

    def test_binding_failure_stops_before_any_model_call(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        root = self.tmp / "tampered2"
        shutil.copytree(task.source_dir, root)
        (root / "auto_ingest" / "shorts" / "cli.py").write_text("# tampered\n")
        runner = self.runner(
            [ScriptedResponse(content='{"patch": "", "tests": [], "assumptions": [], "confidence": 0.5}')],
            source_root=root,
        )
        result = runner.run_task(task, ["single", "swarm"])
        self.assertOutcome(result.attempts[0], Outcome.SOURCE_MISMATCH)
        self.assertEqual(result.attempts[0].metrics.model_calls, 0)
        self.assertEqual(len(result.task_metrics.attempts), 1)
        self.assertEqual(
            (self.run_dir.path / "swarm" / "metrics.json").exists(), False,
            "a swarm attempt must not produce artifacts after a binding failure",
        )

    def test_tampered_source_manifest_is_detected(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        manifest = json.loads(task.source_manifest_path.read_text())
        manifest["files"][0]["sha256"] = "0" * 64
        scratch = self.tmp / "manifest-fixture"
        if scratch.exists():
            shutil.rmtree(scratch)
        shutil.copytree(task.root, scratch)
        (scratch / "source-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
        binding = verify_source_binding(load_task(scratch / "task.json"))
        self.assertFalse(binding.ok)
        self.assertIs(binding.outcome, Outcome.SOURCE_MISMATCH)


if __name__ == "__main__":
    unittest.main()
