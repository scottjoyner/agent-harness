"""AssistX read-only host diagnostic guarantees HOLD regardless of health."""
import unittest
from unittest.mock import patch

from assistx_operator_probe import PATHS, PROCESSES, observe, validate


class AssistXOperatorProbeTests(unittest.TestCase):
    def run_fixture(self, processes, statuses, serve_port):
        with (patch("assistx_operator_probe._processes", return_value=processes),
              patch("assistx_operator_probe._target_status",
                    side_effect=lambda url: statuses[list(PATHS.values()).index(url)]),
              patch("assistx_operator_probe._serve_port",
                    return_value=serve_port)):
            return observe()

    def test_running_but_unreachable_is_hold(self):
        record = self.run_fixture({k:True for k in PROCESSES},
                                  [None,None,404],8081)
        self.assertTrue(validate(record))
        self.assertEqual(record["decision"],"HOLD")
        self.assertFalse(record["agent_dispatch_authorized"])
        self.assertIn("assistx_host_health_not_http_200",record["blockers"])
        self.assertEqual(record["host_local_health_http_status"]["serve_backend"],404)

    def test_even_all_healthy_stays_hold(self):
        record = self.run_fixture({k:True for k in PROCESSES},
                                  [200,200,200],8000)
        self.assertTrue(validate(record))
        self.assertFalse(record["production_deployment_authorized"])
        self.assertFalse(record["authenticated_catalog_verified"])
        self.assertFalse(record["source_authoritative_transactions_verified"])
        self.assertEqual(record["decision"],"HOLD")

    def test_no_services_present_stays_hold(self):
        record = self.run_fixture({k:False for k in PROCESSES},
                                  [None,None,None],None)
        self.assertTrue(validate(record))
        self.assertEqual(record["provider_requests_forwarded"],0)
        self.assertFalse(record["shell_executor_authorized"])

    def test_invalid_or_missing_process_observation_holds(self):
        record = self.run_fixture({k:None for k in PROCESSES},
                                  [403,500,302],8081)
        self.assertTrue(validate(record))
        self.assertIn("assistx_process_not_independently_confirmed",
                      record["blockers"])

    def test_missing_fields_fail_record_verification(self):
        record = self.run_fixture({k:True for k in PROCESSES},
                                  [200,200,200],8000)
        for key in ("decision","agent_dispatch_authorized","blockers"):
            with self.subTest(key=key):
                self.assertFalse(validate({k:v for k,v in record.items() if k!=key}))

    def test_false_positive_admission_is_invalid(self):
        record = self.run_fixture({k:True for k in PROCESSES},
                                  [200,200,200],8000)
        for key in ("agent_dispatch_authorized",
                    "production_deployment_authorized",
                    "automatic_retry_or_refund_authorized",
                    "authenticated_catalog_verified"):
            with self.subTest(key=key):
                self.assertFalse(validate({**record,key:True}))

    def test_process_count_instead_of_boolean_rejected(self):
        record = self.run_fixture({k:True for k in PROCESSES},
                                  [200,200,200],8000)
        self.assertFalse(validate({
            **record,"process_presence":{
                **record["process_presence"],"assistx_api":1}}))

    def test_status_code_boolean_rejected(self):
        record = self.run_fixture({k:True for k in PROCESSES},
                                  [200,200,200],8000)
        self.assertFalse(validate({**record,"host_local_health_http_status":
                                  {**record["host_local_health_http_status"],
                                   "assistx_host_api":True}}))

    def test_no_provider_forwarding_authorized(self):
        record = self.run_fixture({k:True for k in PROCESSES},
                                  [200,200,200],8000)
        self.assertFalse(validate({**record, "provider_requests_forwarded":1}))

    def test_unsafe_url_not_on_allowlist(self):
        from assistx_operator_probe import _target_status
        with self.assertRaises(ValueError):
            _target_status("https://example.com/health")

    def test_redirect_handler_cannot_follow_a_health_redirect(self):
        from urllib.request import HTTPRedirectHandler, ProxyHandler
        from assistx_operator_probe import _target_status
        captured=[]
        class FakeOpener:
            def open(self, req, timeout):
                from urllib.error import HTTPError
                raise HTTPError(req.full_url,302,"redirect",{},None)
        def fake_build_opener(*handlers):
            captured.extend(handlers)
            return FakeOpener()
        with patch("assistx_operator_probe.build_opener",
                   side_effect=fake_build_opener):
            status=_target_status(PATHS["assistx_host_api"])
        self.assertEqual(status,302)
        self.assertTrue(any(isinstance(h,ProxyHandler) for h in captured))
        redirects=[h for h in captured if isinstance(h,HTTPRedirectHandler)]
        self.assertEqual(len(redirects),1)
        self.assertIsNone(redirects[0].redirect_request(
            None,None,302,"redirect",{},"https://example.com"))

    def test_subprocess_failure_cannot_be_interpreted_as_present(self):
        from assistx_operator_probe import _processes
        with patch("assistx_operator_probe._run_fixed",return_value=None):
            status=_processes()
        self.assertEqual(set(status),set(PROCESSES))
        self.assertTrue(all(v is None for v in status.values()))

    def test_tailscale_serve_target_parser_is_allowlisted(self):
        from assistx_operator_probe import SERVE_TARGET
        sample="https://some-tailnet.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:8081\n"
        self.assertEqual(SERVE_TARGET.search(sample).group(1),"8081")
        self.assertIsNone(SERVE_TARGET.search(
            "|-- / proxy http://some-unexpected-host:8000"))

    def test_ordinary_process_names_do_not_infer_real_admission(self):
        record = self.run_fixture({k:True for k in PROCESSES},
                                  [200,200,200],8000)
        self.assertIn("independent_admission_and_revocation_custody_not_verified",
                      record["blockers"])


if __name__ == "__main__":
    unittest.main()
