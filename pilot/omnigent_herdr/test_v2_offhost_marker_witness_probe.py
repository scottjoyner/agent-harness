"""Deny-only evidence contract for x1↔Destroyer physical V2 stage witness."""
import unittest

from v2_offhost_marker_witness_probe import exit_status, physical


PROOFS = (
    "signed_genesis_witnessed",
    "publication_stage_witnessed",
    "ack_fence_witnessed",
    "delivery_stage_witnessed",
    "rolled_back_observer_rejected_by_newer_witness",
    "stale_witness_writer_denied",
    "offhost_log_rollback_denied_with_newer_anchor",
    "old_witness_log_and_old_anchor_still_match",
    "quarantine_marker_witnessed",
    "remote_process_exits_cleanly",
)
DENIALS = (
    "witness_administratively_independent",
    "worker_source_attested",
    "external_highwater_immutability_verified",
    "persistent_trust_root_verified",
    "production_rollback_resistance_verified",
    "agent_dispatch_authorized",
    "model_egress_authorized",
    "used_real_repository",
)


def positive_fixture():
    return {
        "schema": 1,
        "mode": "synthetic_v2_physical_offhost_marker_witness",
        **{k: True for k in PROOFS},
        **{k: False for k in DENIALS},
        "provider_requests_forwarded": 0,
        "errors": [],
    }


class OffhostPhysicalContractTests(unittest.TestCase):
    def test_qualified_fixture_never_authorizes_model(self):
        v = positive_fixture()
        self.assertEqual(exit_status(v), 0)
        self.assertFalse(v["agent_dispatch_authorized"])
        self.assertFalse(v["production_rollback_resistance_verified"])

    def test_missing_or_negative_physical_evidence_denied(self):
        self.assertEqual(exit_status({}), 2)
        for field in PROOFS:
            with self.subTest(field=field):
                self.assertEqual(
                    exit_status({**positive_fixture(), field: False}), 2)

    def test_forged_administrative_custody_or_model_claim_denied(self):
        for field in DENIALS:
            with self.subTest(field=field):
                self.assertEqual(
                    exit_status({**positive_fixture(), field: True}), 2)

    def test_provider_requests_or_exception_denied(self):
        self.assertEqual(exit_status({
            **positive_fixture(), "provider_requests_forwarded": 1}), 2)
        self.assertEqual(exit_status({
            **positive_fixture(), "errors": ["untrusted observer"]}), 2)

    def test_wrong_remote_or_directory_rejected_without_ssh(self):
        for target, root in (
            ("lenovo", "/home/scott/.cache/og-v2-offhost-abcdef"),
            ("destroyer", "/tmp/unsafe"),
            ("destroyer", "/home/scott/.cache/og-v2-offhost-ABCDEF/../../"),
            ("destroyer", "/home/scott/.cache/og-v2-offhost-ab cde"),
        ):
            with self.subTest(remote=target, root=root):
                result = physical(remote_root=root, remote=target)
                self.assertEqual(result["errors"],
                                 ["unapproved offhost fixture target"])
                self.assertEqual(exit_status(result), 2)


if __name__ == "__main__":
    unittest.main()
