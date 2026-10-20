"""Deny-only independent receipt physical evidence schema, no admission."""
import unittest

from v2_witness_countersign_probe import (
    FALSE, TRUE, exit_status, physical,
)


def qualifying_fixture():
    return {
        "schema": 1, "mode": "synthetic_v2_offhost_countersigned_receipts",
        **{k: True for k in TRUE},
        **{k: False for k in FALSE},
        "provider_requests_forwarded": 0,
        "errors": [],
    }


class V2OffhostCountersignPhysicalContractTests(unittest.TestCase):
    def test_synthetic_evidence_never_allows_inference(self):
        r = qualifying_fixture()
        self.assertEqual(exit_status(r), 0)
        self.assertFalse(r["agent_dispatch_authorized"])
        self.assertFalse(r["production_rollback_resistance_verified"])

    def test_incomplete_recovery_evidence_denied(self):
        self.assertEqual(exit_status({}), 2)
        for key in TRUE:
            with self.subTest(key=key):
                self.assertEqual(exit_status({
                    **qualifying_fixture(), key: False}), 2)

    def test_false_custody_or_dispatch_claim_denied(self):
        for key in FALSE:
            with self.subTest(key=key):
                self.assertEqual(exit_status({
                    **qualifying_fixture(), key: True}), 2)

    def test_nondisposable_host_or_directory_rejected_pre_ssh(self):
        for host, root in (
            ("lenovo", "/home/scott/.cache/og-v2-countersign-a1b2c3"),
            ("destroyer", "/tmp/unsafe"),
            ("destroyer", "/home/scott/.cache/og-v2-countersign-a1b2c3/.."),
            ("destroyer", "/home/scott/.cache/og-v2-countersign-a1b2c!"),
            ("destroyer", ""),
        ):
            with self.subTest(host=host, root=root):
                result = physical(remote_root=root, remote=host)
                self.assertNotEqual(exit_status(result), 0)
                self.assertEqual(result["errors"],
                                 ["unapproved fixture witness target"])

    def test_errors_and_provider_traffic_denied(self):
        self.assertEqual(exit_status({
            **qualifying_fixture(), "errors": ["bad signature"]}), 2)
        self.assertEqual(exit_status({
            **qualifying_fixture(), "provider_requests_forwarded": 1}), 2)

    def test_witness_key_substitution_is_never_admission(self):
        r = qualifying_fixture()
        r["changed_witness_signing_key_rejected"] = False
        self.assertEqual(exit_status(r), 2)

    def test_old_valid_log_plus_old_head_counterexample_is_required(self):
        r = qualifying_fixture()
        r["old_log_with_old_anchor_still_countersignable"] = False
        self.assertEqual(exit_status(r), 2)


if __name__ == "__main__":
    unittest.main()
