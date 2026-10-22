"""Synthetic 3-node physical report cannot be promoted to deployment."""
import unittest
from v2_three_node_custody_probe import (
    TRUE, FALSE, physical, exit_status,
)


def fixture():
    return {
        "schema": 1, "mode": "synthetic_three_node_v2_custody_checkpoint",
        **{k: True for k in TRUE},
        **{k: False for k in FALSE},
        "provider_requests_forwarded": 0, "errors": [],
    }


class ThreeNodeDenyOnlyContractTests(unittest.TestCase):
    def test_research_success_is_not_admission(self):
        data = fixture()
        self.assertEqual(exit_status(data), 0)
        self.assertFalse(data["agent_dispatch_authorized"])
        self.assertFalse(data["independent_custodian_verified"])

    def test_missing_success_property_fails_closed(self):
        for key in TRUE:
            with self.subTest(field=key):
                self.assertEqual(exit_status({**fixture(), key: False}), 2)

    def test_forged_independence_or_dispatch_denied(self):
        for key in FALSE:
            with self.subTest(field=key):
                self.assertEqual(exit_status({**fixture(), key: True}), 2)

    def test_any_provider_forwarding_or_error_fails_closed(self):
        self.assertEqual(exit_status({**fixture(), "provider_requests_forwarded": 1}), 2)
        self.assertEqual(exit_status({**fixture(), "errors": ["unsafe"]}), 2)

    def test_unapproved_remote_root_denied_before_any_ssh(self):
        for w, c in (
            ("/tmp/x", "/tmp/y"),
            ("/home/scott/.cache/og-v2-countersign-ABC123",
             "/tmp/og-v2-custody-ABC123"),
            ("/home/scott/.cache/og-v2-countersign-ABC123",
             "/home/scott/.cache/og-v2-custody-ABC123/.."),
        ):
            with self.subTest(w=w, c=c):
                result = physical(witness_root=w, custody_root=c)
                self.assertEqual(result["errors"], ["unapproved research signing roots"])
                self.assertEqual(exit_status(result), 2)


if __name__ == "__main__":
    unittest.main()
