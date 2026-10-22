"""Offline contract tests for disposable Compose-created Hermes rootless candidate."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import assistx_hermes_compose_physical_canary as canary
import assistx_hermes_container_canary as shell
import assistx_hermes_rootless_compose_guard as compose

IMAGE = "sha256:" + "b"*64
OVERLAY = "sha256:" + "c"*64
PROJECT = "oghermescompose" + "a"*16

class ComposePhysicalContractTests(unittest.TestCase):
    def all_green(self):
        result = canary.capture()
        result.update({k: True for k in canary.CHECKS})
        result.update(physical_canary_executed=True, exact_running_image=IMAGE,
                      reproducible_overlay_image=OVERLAY)
        return result

    def test_default_dry_run_never_invokes_docker(self):
        with patch.object(canary, "call") as cmd:
            rec = canary.capture()
        cmd.assert_not_called()
        self.assertTrue(canary.verify(rec))
        self.assertFalse(rec["physical_canary_executed"])
        self.assertEqual(rec["decision"], "HOLD")

    def test_all_green_still_holds_release(self):
        rec = self.all_green()
        self.assertTrue(canary.verify(rec))
        self.assertFalse(rec["production_deployment_authorized"])
        self.assertEqual(rec["real_agent_tasks_executed"], 0)

    def test_each_physical_check_is_mandatory(self):
        rec = self.all_green()
        for key in canary.CHECKS:
            with self.subTest(key=key):
                self.assertFalse(canary.verify({**rec, key: False}))

    def test_wrong_type_and_unbounded_image_rejected(self):
        for name, bad in (
            ("schema", True), ("physical_canary_executed", 1),
            ("compose_created_container_matches_exact_candidate_image", 1),
            ("production_deployment_authorized", 1),
            ("exact_running_image", "latest"),
            ("reproducible_overlay_image", "sha256:abc"),
            ("provider_requests_forwarded", True),
        ):
            with self.subTest(name=name):
                self.assertFalse(canary.verify({**canary.capture(), name: bad}))

    def test_missing_or_extra_fields_denied(self):
        rec = canary.capture()
        self.assertFalse(canary.verify({**rec, "approved": True}))
        self.assertFalse(canary.verify({
            k:v for k,v in rec.items() if k != "decision"}))

    def test_release_blockers_cannot_be_removed(self):
        rec = self.all_green()
        for blocker in canary.BLOCKERS:
            self.assertFalse(canary.verify({**rec,
                "blockers": [b for b in canary.BLOCKERS if b != blocker]}))

    def test_image_ids_mandatory_when_physical_passes(self):
        rec = self.all_green()
        for field in ("exact_running_image", "reproducible_overlay_image"):
            self.assertFalse(canary.verify({**rec, field: None}))

    def test_physical_failure_still_denied(self):
        with patch.object(canary, "physical_run",
                          side_effect=canary.UnsafeComposePhysical("synthetic")):
            rec = canary.capture(physical=True)
        self.assertTrue(canary.verify(rec))
        self.assertFalse(rec["physical_canary_executed"])
        self.assertEqual(rec["decision"], "HOLD")
        self.assertIn("physical_compose_canary_failed_closed", rec["blockers"])

    def test_mocked_physical_success_remains_hold(self):
        with patch.object(canary, "physical_run",
            return_value=({k: True for k in canary.CHECKS}, IMAGE, OVERLAY)):
            rec = canary.capture(physical=True)
        self.assertTrue(canary.verify(rec))
        self.assertTrue(rec["physical_canary_executed"])
        self.assertEqual(rec["decision"], "HOLD")

    def test_only_random_project_names_accepted(self):
        for bad in ("production", "assistx", PROJECT + "-dev",
                    "oghermescomposeAAAAAAAAAAAAAAAA", "a"*40):
            with self.subTest(bad=bad):
                with self.assertRaises(canary.UnsafeComposePhysical):
                    canary.compose_prefix(bad, {})

    def test_compose_prefix_is_profile_and_env_isolated(self):
        argv = canary.compose_prefix(PROJECT, {})
        self.assertEqual(argv[0:2], ["docker", "compose"])
        self.assertEqual(argv[argv.index("--env-file") + 1], "/dev/null")
        self.assertEqual(argv[argv.index("--project-name") + 1], PROJECT)
        self.assertEqual(argv[argv.index("--profile") + 1], "synthetic-canary")
        self.assertNotIn("up", argv)
        self.assertNotIn("down", argv)

    def test_compose_file_rejects_ancestor_and_repo_mounts(self):
        spec = compose.COMPOSE.read_text()
        self.assertIn("network_mode: none", spec)
        self.assertIn("read_only: true", spec)
        self.assertIn("cap_drop: [ALL]", spec)
        self.assertIn("no-new-privileges:true", spec)
        self.assertIn("HERMES_SYNTHETIC_KNOWLEDGE", spec)
        self.assertNotIn("/media/scott/SSD_4TB", spec)
        self.assertNotIn("/media/scott/NAS5", spec)
        self.assertNotIn("/home/scott/git/auto-assist", spec)
        self.assertNotIn("ssh_config", spec)

    def test_only_two_synthetic_mounts_in_verified_compose(self):
        with tempfile.TemporaryDirectory(
            prefix="og-hermes-containment-", dir=shell.SCRATCH_PARENT) as tmp:
            root = Path(tmp)
            for dest in ("knowledge", "worktree"):
                (root / dest).mkdir()
            ro, rw = root / "knowledge", root / "worktree"
            example = {
                "services": {compose.SERVICE: {
                    "image": IMAGE, "pull_policy": "never", "network_mode": "none",
                    "read_only": True, "user": "1000:1000", "restart": "no",
                    "entrypoint": ["/entrypoint.sh"],
                    "command": ["python3","-B","-m","assistx.strict_executor_main"],
                    "profiles": ["synthetic-canary"], "cap_drop": ["ALL"],
                    "security_opt": ["no-new-privileges:true"],
                    "environment": dict(compose.EXPECTED_ENV),
                    "tmpfs": ["/tmp"], "volumes": [
                        {"type":"bind","source":str(ro),"target":"/research/knowledge","read_only":True},
                        {"type":"bind","source":str(rw),"target":"/workspace","read_only":False}
                    ]}}}
            self.assertTrue(compose.verify_render(example, IMAGE, ro, rw))
            bad = json.loads(json.dumps(example))
            bad["services"][compose.SERVICE]["volumes"][0]["read_only"] = False
            self.assertFalse(compose.verify_render(bad, IMAGE, ro, rw))
            bad = json.loads(json.dumps(example))
            bad["services"][compose.SERVICE]["network_mode"] = "host"
            self.assertFalse(compose.verify_render(bad, IMAGE, ro, rw))

    def test_no_production_compose_destruction_in_runner(self):
        script = Path(canary.__file__).read_text()
        self.assertIn('"create", "--no-build", "--pull", "never"', script)
        self.assertIn('["docker", "start", "--attach", cid]', script)
        self.assertIn('["docker", "rm", "--force", cid]', script)
        self.assertNotIn('"down"', script)
        self.assertNotIn('"compose", "up"', script)
        self.assertIn("normal._inspect", script)
        self.assertIn("normal._exclusive_fixed_base_tag()", script)

    def test_synthetic_mock_disallows_child_executables(self):
        fixture = canary.normal.SITE.read_text()
        self.assertIn('patch.object(subprocess, "Popen"', fixture)
        self.assertIn("forbidden_process", fixture)
        self.assertIn('patch("requests.request"', fixture)
        self.assertIn("ROOTLESS_HERMES_NORMAL_MAIN_PASS", fixture)

if __name__ == "__main__":
    unittest.main()
