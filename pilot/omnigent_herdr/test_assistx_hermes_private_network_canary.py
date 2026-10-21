"""Offline HOLD-only guards for cross-container synthetic Hermes lease canary."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import assistx_hermes_container_canary as shell
import assistx_hermes_normal_startup_canary as normal
import assistx_hermes_private_network_canary as private

IMAGE = "sha256:" + "c"*64
SUFFIX = "a"*16
NET = "og-hermes-net-" + SUFFIX
SERVER_NAME = "og-hermes-net-server-" + SUFFIX
WORKER_NAME = "og-hermes-normal-test-" + SUFFIX

class CrossContainerHoldTests(unittest.TestCase):
    def good(self):
        record = private.capture(False)
        record.update({key: True for key in private.REQUIRED})
        record.update(physical_canary_executed=True,
                      exact_running_image=IMAGE, overlay_image=IMAGE)
        return record

    def test_no_docker_or_production_write_in_dry_run(self):
        with patch.object(private, "call") as called:
            record = private.capture(False)
        called.assert_not_called()
        self.assertTrue(private.validate(record))
        self.assertEqual(record["decision"], "HOLD")

    def test_full_green_is_still_not_release_authority(self):
        record = self.good()
        self.assertTrue(private.validate(record))
        self.assertFalse(record["production_deployment_authorized"])
        self.assertEqual(record["provider_requests_forwarded"], 0)
        self.assertEqual(record["real_agent_tasks_executed"], 0)

    def test_every_physical_check_is_mandatory(self):
        record = self.good()
        for check in private.REQUIRED:
            with self.subTest(check=check):
                self.assertFalse(private.validate({**record, check: False}))

    def test_release_or_provider_claim_rejected(self):
        record = self.good()
        for key, bad in (
            ("production_deployment_authorized", True),
            ("decision", "GO"),
            ("provider_requests_forwarded", 1),
            ("real_agent_tasks_executed", 1),
        ):
            with self.subTest(key=key):
                self.assertFalse(private.validate({**record, key: bad}))

    def test_wrong_types_rejected(self):
        record = private.capture()
        for key, bad in (
            ("schema", True), ("physical_canary_executed", 1),
            ("production_hermes_running_unchanged", "true"),
            ("provider_requests_forwarded", False),
            ("private_internal_bridge_verified", 1),
            ("exact_running_image", "latest"),
            ("overlay_image", "sha256:nope"),
        ):
            with self.subTest(key=key):
                self.assertFalse(private.validate({**record, key: bad}))

    def test_no_missing_or_extra_evidence_keys(self):
        record = private.capture()
        self.assertFalse(private.validate({**record, "release": True}))
        self.assertFalse(private.validate({k: v for k,v in record.items()
                                          if k != "decision"}))

    def test_blockers_must_remain_even_when_green(self):
        record = self.good()
        for blocker in private.BLOCKERS:
            with self.subTest(blocker=blocker):
                self.assertFalse(private.validate({**record,
                    "blockers": [x for x in private.BLOCKERS if x != blocker]}))

    def test_success_needs_both_image_ids(self):
        record = self.good()
        self.assertFalse(private.validate({**record, "overlay_image": None}))
        self.assertFalse(private.validate({**record, "exact_running_image": None}))

    def test_fake_physical_failure_stays_hold(self):
        with patch.object(private, "execute",
                          side_effect=private.UnsafePrivateNet("synthetic")):
            record = private.capture(physical=True)
        self.assertTrue(private.validate(record))
        self.assertFalse(record["physical_canary_executed"])
        self.assertIn("private_network_physical_canary_failed_closed",
                      record["blockers"])

    def test_mocked_success_still_hold(self):
        with patch.object(private, "execute",
                          return_value=({k: True for k in private.REQUIRED},
                                        IMAGE, IMAGE)):
            record = private.capture(physical=True)
        self.assertTrue(private.validate(record))
        self.assertTrue(record["physical_canary_executed"])
        self.assertEqual(record["decision"], "HOLD")

    def test_rejects_untrusted_network_name(self):
        for value in ("bridge", "assistx-fleet_default",
                      "og-hermes-net-NOTHEX", NET + "-other"):
            with self.subTest(value=value):
                with self.assertRaises(private.UnsafePrivateNet):
                    private.network_ok(value)

    def test_worker_is_normal_entrypoint_without_override(self):
        with tempfile.TemporaryDirectory(
                prefix="og-hermes-normal-fixture-",
                dir=shell.SCRATCH_PARENT) as temp:
            knowledge = Path(temp) / "knowledge"
            work = Path(temp) / "worker"
            knowledge.mkdir(); work.mkdir()
            argv = private.worker_argv(IMAGE, WORKER_NAME, NET,
                                       SERVER_NAME, knowledge, work)
        self.assertIn("--network=" + NET, argv)
        self.assertNotIn("--network=none", argv)
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertNotIn("--entrypoint", argv)
        self.assertEqual(argv[-4:], [
            "python3", "-B", "-m", "assistx.strict_executor_main"])
        self.assertIn("--env=FAKE_ASSISTX_URL=http://" + SERVER_NAME + ":18181", argv)
        self.assertNotIn("SSD_4TB", " ".join(argv))
        self.assertNotIn("NAS5", " ".join(argv))

    def test_worker_rejects_non_synthetic_server(self):
        with tempfile.TemporaryDirectory(
                prefix="og-hermes-normal-fixture-",
                dir=shell.SCRATCH_PARENT) as temp:
            knowledge = Path(temp) / "knowledge"
            work = Path(temp) / "worker"
            knowledge.mkdir(); work.mkdir()
            with self.assertRaises(private.UnsafePrivateNet):
                private.worker_argv(IMAGE, WORKER_NAME, NET, "production",
                                    knowledge, work)

    def test_server_has_no_published_ports_or_prod_mount(self):
        with tempfile.TemporaryDirectory(
                prefix="og-hermes-normal-fixture-",
                dir=shell.SCRATCH_PARENT) as temp:
            knowledge = Path(temp) / "knowledge"
            work = Path(temp) / "server"
            knowledge.mkdir(); work.mkdir()
            argv = private.server_argv(IMAGE, SERVER_NAME, NET,
                                      knowledge, work)
        self.assertIn("--network=" + NET, argv)
        self.assertIn("--read-only", argv)
        self.assertIn("--user=1000:1000", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertNotIn("-p", argv)
        self.assertNotIn("--publish", argv)
        self.assertEqual(argv[-4:],
            ["-B", "/workspace/hermes_private_net_api_server.py",
             "18181", "/workspace/fake-assistx-fault-state.json"])

    def test_server_rejects_real_host_path(self):
        with tempfile.TemporaryDirectory() as temp:
            knowledge=Path(temp)/"knowledge";knowledge.mkdir()
            work=Path(temp)/"server";work.mkdir()
            with self.assertRaises(private.UnsafePrivateNet):
                private.server_argv(IMAGE, SERVER_NAME, NET, knowledge, work)

    def test_private_network_creation_requires_internal(self):
        script = Path(private.__file__).read_text()
        self.assertIn('"--internal"', script)
        self.assertIn('doc.get("Internal") is True', script)
        self.assertIn('doc.get("Attachable") is False', script)
        self.assertIn("PortBindings", script)
        self.assertIn("normal._exclusive_fixed_base_tag()", script)

    def test_server_process_and_worker_stay_separate(self):
        script = Path(private.__file__).read_text()
        self.assertIn('["docker", "stop", "--time=2", server_name]', script)
        self.assertIn('["docker", "start", server_name]', script)
        self.assertIn('["docker", "start", "--attach", worker_name]', script)
        self.assertIn('["docker", "network", "rm", network]', script)

    def test_worker_enforces_stale_replay_checks(self):
        source = private.WORKER.read_text()
        self.assertIn("old_token_rejected", source)
        self.assertIn("stale_claim_rejected", source)
        self.assertIn("completion_replay_rejected", source)
        self.assertIn("NoNewPrivs", source)
        self.assertIn('patch.object(subprocess, "Popen"', source)

    def test_server_source_is_synthetic_only(self):
        source = private.SERVER.read_text()
        self.assertIn('("0.0.0.0", PORT)', source)
        self.assertIn('"/workspace/fake-assistx-fault-state.json"', source)
        self.assertIn('"/__fixture/tick"', source)
        self.assertIn('"CANCELLED"', source)
        self.assertNotIn("docker compose", source.lower())
        self.assertNotIn("http://host.docker.internal", source.lower())

if __name__ == "__main__":
    unittest.main()
