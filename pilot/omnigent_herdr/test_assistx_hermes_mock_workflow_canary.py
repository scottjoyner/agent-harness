"""Deny-by-default contracts for the disposable Hermes Python workflow."""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import assistx_hermes_mock_workflow_canary as candidate
import assistx_hermes_container_canary as shell


class WorkflowCanaryContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.root = self.parent / "og-hermes-containment-Aa_09Zx1"
        self.root.mkdir()
        self.knowledge = self.root / "knowledge"
        self.workspace = self.root / "worktree"
        self.knowledge.mkdir()
        self.workspace.mkdir()
        self.name = "og-hermes-image-canary-" + "b"*20
        self.image = "sha256:" + "a"*64

    def argv(self):
        with patch.object(shell, "SCRATCH_PARENT", self.parent):
            return candidate.make_argv(
                self.image, self.name, self.knowledge, self.workspace
            )

    def test_exact_image_never_pulls_or_calls_agent_entrypoint(self):
        cmd = self.argv()
        self.assertIn("--pull=never", cmd)
        self.assertIn("--network=none", cmd)
        self.assertIn("--read-only", cmd)
        self.assertIn("--cap-drop=ALL", cmd)
        self.assertIn("--security-opt=no-new-privileges:true", cmd)
        self.assertIn("--entrypoint=/bin/sh", cmd)
        self.assertIn("--pids-limit=32", cmd)
        self.assertIn("--memory=256m", cmd)
        self.assertIn(f"--user={os.getuid()}:{os.getgid()}", cmd)
        self.assertIn(self.image, cmd)
        self.assertEqual(cmd[-2:], ["-c", "python3 -B /workspace/smoke.py"])
        self.assertNotIn("strict_executor_main", " ".join(cmd))

    def test_mounts_exactly_synthetic_ro_and_rw(self):
        cmd = self.argv()
        mounts = [cmd[i+1] for i, arg in enumerate(cmd[:-1]) if arg == "--mount"]
        self.assertEqual(mounts, [
            f"type=bind,src={self.knowledge},dst=/research/knowledge,readonly",
            f"type=bind,src={self.workspace},dst=/workspace",
        ])
        for forbidden in ("/home/scott/knowledge", "/media/scott",
                          "/home/scott/.ssh", "docker.sock", "/home/scott/git"):
            self.assertNotIn(forbidden, " ".join(mounts))

    def test_fails_unsafe_mount_and_unpinned_image(self):
        with patch.object(shell, "SCRATCH_PARENT", self.parent):
            with self.assertRaises(shell.UnsafeCanary):
                candidate.make_argv("latest", self.name, self.knowledge, self.workspace)
            with self.assertRaises(shell.UnsafeCanary):
                candidate.make_argv(self.image, self.name,
                                    Path("/home/scott/knowledge"), self.workspace)

    def test_synthetic_only_http_and_local_writes(self):
        p = candidate.SMOKE
        self.assertIn("entry.install_strict_executor_adapter", p)
        self.assertIn("agent.run_loop(once=True)", p)
        self.assertIn('len(calls) == 1', p)
        self.assertIn("agent.record_task_eval", p)
        self.assertIn("/research/knowledge/forbidden", p)
        self.assertIn("/etc/hermes-canary-forbidden", p)
        self.assertIn("Bearer SYNTHETIC_BOOTSTRAP_ONLY", p)
        for forbidden in ("subprocess.Popen(", "os.system(", "socket.connect(",
                          "hermes-agent --", "ssh ", "curl ", "http://host.docker"):
            self.assertNotIn(forbidden, p)
        self.assertEqual(candidate.capture()["decision"], "HOLD")

    def test_no_real_provider_credentials_or_endpoints(self):
        vals = candidate.SYNTHETIC_ENV
        self.assertIn("HERMES_SELFTASK_ENABLED=false", vals)
        self.assertIn("ASSISTX_URL=http://127.0.0.1:1", vals)
        self.assertTrue(all("sk-" not in s for s in vals))
        self.assertTrue(all("http://host.docker" not in s for s in vals))
        self.assertEqual(len(vals), len(set(vals)))

    def test_default_never_executes_docker(self):
        with patch.object(shell, "_exec") as execute:
            rec = candidate.capture()
        execute.assert_not_called()
        self.assertFalse(rec["physical_canary_executed"])
        self.assertTrue(candidate.verify(rec))

    def test_wrong_node_does_not_inspect_or_start(self):
        with patch.object(candidate.os, "uname") as host, \
             patch.object(shell, "_image_id") as get_image:
            host.return_value.nodename = "beelink"
            rec = candidate.capture(physical=True)
        get_image.assert_not_called()
        self.assertFalse(rec["physical_canary_executed"])
        self.assertIn("synthetic_python_workflow_failed_closed", rec["blockers"])

    def test_forged_production_authority_never_valid(self):
        baseline = candidate.capture()
        for key, val in (
            ("production_deployment_authorized", True),
            ("production_containers_modified", True),
            ("provider_requests_forwarded", 1),
            ("real_agent_tasks_executed", 1),
            ("decision", "GO"),
        ):
            with self.subTest(key=key):
                record = copy.deepcopy(baseline)
                record[key] = val
                self.assertFalse(candidate.verify(record))

    def test_partial_and_malformed_records_denied(self):
        baseline = candidate.capture()
        record = dict(baseline)
        record["synthetic_strict_poll_and_scoped_writes_passed"] = True
        self.assertFalse(candidate.verify(record))
        for value in (True, 1, "sha256:fake"):
            with self.subTest(value=value):
                altered = dict(baseline)
                altered["exact_running_image_object_sha256"] = value
                self.assertFalse(candidate.verify(altered))
        altered = dict(baseline)
        altered["blockers"] = []
        self.assertFalse(candidate.verify(altered))

    def test_staged_physical_success_still_hold(self):
        record = candidate.capture()
        record.update({
            "physical_canary_executed": True,
            "synthetic_strict_poll_and_scoped_writes_passed": True,
            "exact_running_image_object_sha256": self.image,
        })
        self.assertTrue(candidate.verify(record))
        self.assertFalse(record["production_deployment_authorized"])
        self.assertEqual(record["decision"], "HOLD")


if __name__ == "__main__":
    unittest.main()
