"""Negative contract tests for isolated synthetic Hermes task lifecycle."""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import assistx_hermes_task_lifecycle_canary as life
import assistx_hermes_container_canary as shell
import assistx_hermes_mock_workflow_canary as prior


class SyntheticLifecycleContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "og-hermes-containment-Aa_09Zx1"
        self.root.mkdir()
        self.ro = self.root / "knowledge"
        self.rw = self.root / "worktree"
        self.ro.mkdir()
        self.rw.mkdir()
        self.image = "sha256:" + "a"*64
        self.name = "og-hermes-image-canary-" + "b"*20

    def argv(self):
        with patch.object(shell, "SCRATCH_PARENT", self.parent):
            return prior.make_argv(self.image, self.name, self.ro, self.rw)

    def test_only_synthetic_mounts_preserved(self):
        cmd = self.argv()
        mounts = [cmd[i+1] for i, val in enumerate(cmd[:-1]) if val == "--mount"]
        self.assertEqual(len(mounts), 2)
        self.assertIn(f"src={self.ro},dst=/research/knowledge,readonly", mounts[0])
        self.assertIn(f"src={self.rw},dst=/workspace", mounts[1])
        for forbidden in ("/media/scott/SSD_4TB", "/media/scott/NAS5",
                          "/home/scott/.ssh", "/home/scott/knowledge"):
            self.assertNotIn(forbidden, " ".join(mounts))

    def test_no_network_and_nonroot(self):
        cmd = self.argv()
        for key in ("--network=none", "--read-only", "--cap-drop=ALL",
                    "--pull=never", "--security-opt=no-new-privileges:true",
                    "--entrypoint=/bin/sh", f"--user={os.getuid()}:{os.getgid()}"):
            self.assertIn(key, cmd)

    def test_mock_exact_lifecycle_contract(self):
        program = life.SMOKE
        for phrase in ("entry.install_strict_executor_adapter(agent)",
                       "agent.run_loop(once=True)", "client.claim_task",
                       '"heartbeat"', '"complete"', "client._lease_lost.set()",
                       '"different claim identity"', '"executor_task_token_missing"',
                       "except PermissionError"):
            self.assertIn(phrase, program)
        self.assertIn('expected = ["poll", "claim", "token", "heartbeat", "context",', program)

    def test_python_process_and_requests_fail_closed(self):
        p = life.SMOKE
        self.assertIn('patch("requests.request", side_effect=http)', p)
        self.assertIn('patch.object(subprocess, "Popen", side_effect=forbid_process)', p)
        self.assertIn('assert url.startswith("http://127.0.0.1:1/")', p)
        self.assertIn('patch.object(agent, "fleet", None)', p)
        self.assertIn('patch.object(agent, "SELFTASK_BULK_MODELS", [])', p)
        self.assertNotIn("shell=True", p)

    def test_no_real_credentials_configured(self):
        env = prior.SYNTHETIC_ENV
        self.assertIn("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN=SYNTHETIC_BOOTSTRAP_ONLY", env)
        self.assertIn("HERMES_SELFTASK_ENABLED=false", env)
        self.assertNotIn("OPENAI_API_KEY=", " ".join(env))
        self.assertNotIn("http://host.docker.internal", " ".join(env))

    def test_default_hold_no_docker_invocation(self):
        with patch.object(shell, "_exec") as run:
            record = life.capture()
        run.assert_not_called()
        self.assertTrue(life.verify(record))
        self.assertFalse(record["physical_canary_executed"])
        self.assertEqual(record["decision"], "HOLD")

    def test_wrong_host_no_docker_inspect(self):
        with patch.object(life.os, "uname") as name, \
             patch.object(shell, "_image_id") as inspect:
            name.return_value.nodename = "beelink"
            report = life.capture(physical=True)
        inspect.assert_not_called()
        self.assertFalse(report["physical_canary_executed"])
        self.assertIn("synthetic_task_lifecycle_failed_closed", report["blockers"])

    def test_synthetic_success_cannot_be_production_authority(self):
        report = life.capture()
        report["physical_canary_executed"] = True
        report["synthetic_claim_heartbeat_complete_and_revoke_passed"] = True
        report["image_id"] = self.image
        self.assertTrue(life.verify(report))
        self.assertFalse(report["production_deployment_authorized"])
        self.assertFalse(report["production_containers_modified"])
        self.assertEqual(report["decision"], "HOLD")

    def test_false_authorities_all_denied(self):
        data = life.capture()
        for key, value in (
            ("decision", "GO"),
            ("production_deployment_authorized", True),
            ("production_containers_modified", True),
            ("provider_requests_forwarded", 1),
            ("real_agent_tasks_executed", 1),
        ):
            with self.subTest(key=key):
                changed = dict(data)
                changed[key] = value
                self.assertFalse(life.verify(changed))

    def test_partial_report_denied(self):
        data = life.capture()
        changed = dict(data)
        changed["synthetic_claim_heartbeat_complete_and_revoke_passed"] = True
        self.assertFalse(life.verify(changed))
        changed = dict(data)
        changed["physical_canary_executed"] = True
        self.assertFalse(life.verify(changed))

    def test_bad_image_id_denied(self):
        baseline = life.capture()
        for val in ("latest", "sha256:" + "z"*64, True, 1):
            with self.subTest(value=val):
                changed = dict(baseline)
                changed["image_id"] = val
                self.assertFalse(life.verify(changed))

    def test_missing_blockers_denied(self):
        report = life.capture()
        for blocker in life.BLOCKERS:
            with self.subTest(blocker=blocker):
                changed = copy.deepcopy(report)
                changed["blockers"].remove(blocker)
                self.assertFalse(life.verify(changed))


if __name__ == "__main__":
    unittest.main()
