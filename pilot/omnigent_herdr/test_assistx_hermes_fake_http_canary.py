"""Fail-closed contract tests for Hermes real-loopback-HTTP restart canary."""
from __future__ import annotations
import unittest
from unittest.mock import patch
import assistx_hermes_fake_http_canary as http
import assistx_hermes_container_canary as shell
import assistx_hermes_normal_startup_canary as normal

IMAGE = "sha256:" + "a" * 64

class HermesHttpCanaryTests(unittest.TestCase):
    def test_dry_run_is_deny_only_and_no_docker(self):
        with patch.object(http, "_call") as mock:
            r = http.capture()
        mock.assert_not_called()
        self.assertTrue(http.validate(r))
        self.assertEqual(r["decision"], "HOLD")
        self.assertFalse(r["physical_canary_executed"])
        self.assertEqual(r["provider_requests_forwarded"], 0)

    def test_each_check_is_required_for_physical_success(self):
        r = http.capture()
        r.update({k: True for k in http.CHECKS})
        r.update(physical_canary_executed=True,
                 exact_running_image=IMAGE, reproducible_overlay_image=IMAGE)
        self.assertTrue(http.validate(r))
        for key in http.CHECKS:
            with self.subTest(key=key):
                self.assertFalse(http.validate({**r, key: False}))

    def test_false_release_claims_rejected(self):
        for key, fake in (
            ("decision", "GO"),
            ("production_deployment_authorized", True),
            ("provider_requests_forwarded", 1),
            ("real_agent_tasks_executed", 1),
            ("physical_canary_executed", True),
        ):
            with self.subTest(key=key):
                self.assertFalse(http.validate({**http.capture(), key: fake}))

    def test_no_bogus_types(self):
        for key, fake in (
            ("schema", True), ("physical_canary_executed", 1),
            ("real_http_claim_heartbeat_completion", "true"),
            ("provider_requests_forwarded", False),
            ("exact_running_image", "latest"),
            ("reproducible_overlay_image", "sha256:xyz"),
        ):
            with self.subTest(key=key):
                self.assertFalse(http.validate({**http.capture(), key: fake}))

    def test_blockers_cannot_be_removed(self):
        sample = http.capture()
        self.assertFalse(http.validate({**sample, "blockers": []}))
        for blocker in http.BLOCKERS:
            self.assertFalse(http.validate({
                **sample, "blockers": [x for x in http.BLOCKERS if x != blocker]}))

    def test_extra_or_missing_fields_are_rejected(self):
        sample = http.capture()
        self.assertFalse(http.validate({**sample, "is_live": True}))
        self.assertFalse(http.validate({
            k: v for k, v in sample.items() if k != "decision"}))

    def test_image_provenance_required_even_after_check_pass(self):
        sample = http.capture()
        sample.update({k: True for k in http.CHECKS})
        sample["physical_canary_executed"] = True
        self.assertFalse(http.validate(sample))
        sample["exact_running_image"] = IMAGE
        self.assertFalse(http.validate(sample))
        sample["reproducible_overlay_image"] = IMAGE
        self.assertTrue(http.validate(sample))

    def test_capture_failure_closes_without_risky_claim(self):
        with patch.object(http, "_run", side_effect=http.UnsafeHttpCanary("synthetic")):
            report = http.capture(physical=True)
        self.assertTrue(http.validate(report))
        self.assertEqual(report["decision"], "HOLD")
        self.assertFalse(report["physical_canary_executed"])
        self.assertIn("physical_http_canary_failed_closed", report["blockers"])

    def test_physical_success_never_approves_release(self):
        with patch.object(http, "_run", return_value=(
            {k: True for k in http.CHECKS}, IMAGE, IMAGE)):
            report = http.capture(physical=True)
        self.assertTrue(http.validate(report))
        self.assertTrue(report["physical_canary_executed"])
        self.assertFalse(report["production_deployment_authorized"])

    def test_fixture_is_checked_in_test_only_not_a_production_entrypoint(self):
        script = http.SITE.read_text()
        self.assertIn("ThreadingHTTPServer", script)
        self.assertIn('("127.0.0.1", 0)', script)
        self.assertIn("strict_run_hermes", script)
        self.assertIn('patch.object(subprocess, "Popen"', script)
        self.assertIn("ROOTLESS_HERMES_HTTP_RESTART_PASS", script)
        self.assertIn("status_code == 409", script)
        self.assertIn("JOURNAL", script)
        self.assertNotIn("docker compose up", script.lower())

    def test_prod_container_is_never_named_as_disposable(self):
        self.assertNotEqual(shell.IMAGE_NAME, "og-hermes-normal-test-" + "0"*16)
        self.assertEqual(normal.STABLE_BASE_TAG, "og-hermes-normal-base-" + "0"*16)
        self.assertEqual(http.CHECKS[-1], "production_hermes_unchanged")

    def test_required_checks_are_unique(self):
        self.assertEqual(len(http.CHECKS), len(set(http.CHECKS)))
        self.assertTrue(set(http.CHECKS).isdisjoint(set(http.BLOCKERS)))

    def test_bad_stage_fails_closed(self):
        with self.assertRaises(http.UnsafeHttpCanary):
            http._must(False, "synthetic mismatch")

if __name__ == "__main__":
    unittest.main()
