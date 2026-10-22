"""Offline strict HOLD-only contract tests for isolated fake AssistX failure injection."""
from __future__ import annotations
import unittest
from unittest.mock import patch
import assistx_hermes_fault_process_canary as fault

IMAGE = "sha256:" + "c" * 64

class FaultCanaryContractTests(unittest.TestCase):
    def good(self):
        record = fault.capture()
        record.update({k: True for k in fault.REQUIRED})
        record.update(physical_canary_executed=True,
                      exact_running_image=IMAGE, overlay_image=IMAGE)
        return record

    def test_dry_run_cannot_touch_docker(self):
        with patch.object(fault, "_call") as call:
            result = fault.capture()
        call.assert_not_called()
        self.assertTrue(fault.validate(result))
        self.assertFalse(result["physical_canary_executed"])
        self.assertEqual(result["decision"], "HOLD")

    def test_all_passes_still_prohibit_production(self):
        record = self.good()
        self.assertTrue(fault.validate(record))
        self.assertFalse(record["production_deployment_authorized"])

    def test_every_check_required(self):
        expected = self.good()
        for name in fault.REQUIRED:
            with self.subTest(name=name):
                self.assertFalse(fault.validate({**expected, name: False}))

    def test_missing_independent_process_identity_fails(self):
        record = self.good()
        self.assertFalse(fault.validate({**record, "separate_fake_http_server": False}))

    def test_fake_head_not_an_image_pin(self):
        record = self.good()
        for bad in ("latest", "sha256:abc", "sha256:" + "z" * 64, True, 99):
            with self.subTest(bad=bad):
                self.assertFalse(fault.validate({**record, "overlay_image": bad}))

    def test_wrong_types_do_not_pretend_success(self):
        for key, bad in (("schema", True), ("physical_canary_executed", 1),
                         ("provider_requests_forwarded", True),
                         ("production_deployment_authorized", 1),
                         ("separate_fake_http_server", "true")):
            self.assertFalse(fault.validate({**fault.capture(), key: bad}))

    def test_required_release_blockers_cannot_be_omitted(self):
        for item in fault.BLOCKERS:
            self.assertFalse(fault.validate({
                **fault.capture(),
                "blockers": [x for x in fault.BLOCKERS if x != item]}))

    def test_unknown_or_missing_fields_rejected(self):
        record = fault.capture()
        self.assertFalse(fault.validate({**record, "allowed_to_deploy": True}))
        self.assertFalse(fault.validate({
            k: v for k, v in record.items() if k != "decision"}))

    def test_dry_record_cannot_claim_success(self):
        self.assertFalse(fault.validate({
            **fault.capture(), "physical_canary_executed": True}))

    def test_fake_success_requires_both_image_ids(self):
        a = self.good()
        a["overlay_image"] = None
        self.assertFalse(fault.validate(a))
        a["overlay_image"] = IMAGE
        a["exact_running_image"] = None
        self.assertFalse(fault.validate(a))

    def test_failure_is_hold_even_if_physical_requested(self):
        with patch.object(fault, "run_physical",
                          side_effect=fault.UnsafeFaultCanary("synthetic")):
            result = fault.capture(physical=True)
        self.assertTrue(fault.validate(result))
        self.assertFalse(result["physical_canary_executed"])
        self.assertIn("disposable_fake_api_process_failed_closed", result["blockers"])

    def test_mocked_full_checks_remain_hold(self):
        with patch.object(fault, "run_physical",
                          return_value=({k: True for k in fault.REQUIRED},
                                        IMAGE, IMAGE)):
            r = fault.capture(physical=True)
        self.assertTrue(fault.validate(r))
        self.assertTrue(r["physical_canary_executed"])
        self.assertEqual(r["decision"], "HOLD")

    def test_fixture_isolated_python_process(self):
        content = fault.SITE.read_text()
        self.assertIn("subprocess.Popen(args", content)
        self.assertIn('"127.0.0.1"', content)
        self.assertIn('"-I", "-B"', content)
        self.assertIn("proc.kill()", content)
        self.assertIn("requests.ConnectionError", content)
        self.assertIn("HERMES_INDEPENDENT_FAKE_API_FAULT_PASS", content)

    def test_server_fixed_host_and_synthetic_path(self):
        source = fault.SERVER.read_text()
        self.assertIn('("127.0.0.1", PORT)', source)
        self.assertIn('"/workspace/fake-assistx-fault-state.json"', source)
        self.assertIn('"/__fixture/tick"', source)
        self.assertIn('"CANCELLED"', source)
        self.assertIn("return self.respond(409", source)
        self.assertNotIn("docker compose", source.lower())

    def test_image_build_and_entrypoint_reuse_original_boundaries(self):
        source = __import__("pathlib").Path(fault.__file__).read_text(encoding="utf-8")
        self.assertIn("normal._container_argv", source)
        self.assertIn("normal._inspect", source)
        self.assertIn("normal._exclusive_fixed_base_tag", source)
        self.assertIn("normal._build_argv", source)
        self.assertIn("all_temporary_docker_objects_removed", source)

    def test_no_live_credentials_in_fixture(self):
        for source in (fault.SERVER.read_text(), fault.SITE.read_text()):
            self.assertNotIn("AUTO_ASSIST_TOKEN=", source)
            self.assertNotIn("docker.sock", source)
            self.assertNotIn("ssh ", source)

if __name__ == "__main__":
    unittest.main()
