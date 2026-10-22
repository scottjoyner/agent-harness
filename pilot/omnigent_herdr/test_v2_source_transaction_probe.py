"""Physical source canary cannot grant production admission."""
import unittest
from v2_source_transaction_probe import TRUE, FALSE, exit_status, physical


def success_fixture():
    return {
        "schema": 1, "mode": "synthetic_v2_resource_side_commit_probe",
        **{k: True for k in TRUE},
        **{k: False for k in FALSE},
        "provider_requests_forwarded": 0, "errors": [],
    }


class PhysicalResourceReceiptContractTests(unittest.TestCase):
    def test_green_synthetic_fixture_does_not_grant_agent(self):
        result = success_fixture()
        self.assertEqual(exit_status(result), 0)
        self.assertFalse(result["agent_dispatch_authorized"])
        self.assertFalse(result["production_resource_transaction_attested"])

    def test_missing_physical_proof_denied(self):
        for k in TRUE:
            with self.subTest(check=k):
                self.assertEqual(exit_status({**success_fixture(), k:False}), 2)

    def test_false_independence_or_model_dispatched_rejected(self):
        for k in FALSE:
            with self.subTest(check=k):
                self.assertEqual(exit_status({**success_fixture(), k:True}), 2)

    def test_any_provider_traffic_or_errors_denied(self):
        self.assertEqual(exit_status({
            **success_fixture(), "provider_requests_forwarded":1}), 2)
        self.assertEqual(exit_status({
            **success_fixture(), "errors":["source uncertainty"]}), 2)

    def test_wrong_host_or_path_refused_without_ssh(self):
        for host, root in (
            ("lenovo", "/home/scott/.cache/og-v2-source-Ab91zQ"),
            ("destroyer", "/tmp/fake"),
            ("destroyer", "/home/scott/.cache/og-v2-source-Ab91zQ/.."),
        ):
            with self.subTest(host=host, root=root):
                r=physical(remote_root=root, remote=host)
                self.assertNotEqual(exit_status(r), 0)
                self.assertEqual(r["errors"],
                                 ["unapproved physical resource fixture"])


if __name__ == "__main__":
    unittest.main()
