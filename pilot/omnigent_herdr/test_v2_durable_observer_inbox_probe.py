"""Deny-only result contract for disposable V2 observer process restarts."""
import unittest

from v2_durable_observer_inbox_probe import exit_status, physical

PASS_FIELDS = (
    "worker_v2_atomic_outbox_bound",
    "publication_fence_survives_process_restart",
    "signed_ack_fence_survives_process_restart",
    "lost_ack_cannot_be_reissued",
    "forged_delivery_cannot_clear_fence",
    "valid_delivery_survives_process_restart",
    "stale_anchor_denied",
    "newer_anchor_detects_old_valid_db",
    "old_db_and_old_anchor_still_match",
    "unverified_publication_quarantine_survived_restart",
)
NOT_PROVEN = (
    "signing_keys_persisted", "independent_worker_identity_verified",
    "durable_external_anchor_verified", "source_transaction_attested",
    "rollback_resistance_verified", "agent_dispatch_authorized",
    "model_egress_authorized", "used_real_repository",
)


def synthetic_result():
    return {
        "schema": 1, "mode": "synthetic_restart_durable_v2_observer_inbox",
        **{field: True for field in PASS_FIELDS},
        **{field: False for field in NOT_PROVEN},
        "provider_requests_forwarded": 0, "errors": [],
    }


class V2DurableObserverProbeTests(unittest.TestCase):
    def test_success_still_denies_all_agents(self):
        self.assertEqual(exit_status(synthetic_result()), 0)
        self.assertFalse(synthetic_result()["agent_dispatch_authorized"])

    def test_missing_required_evidence_denied(self):
        self.assertEqual(exit_status({}), 2)
        for field in PASS_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(exit_status({
                    **synthetic_result(), field: False}), 2)

    def test_fake_rollback_protection_or_custody_denied(self):
        for field in NOT_PROVEN:
            with self.subTest(field=field):
                self.assertEqual(exit_status({
                    **synthetic_result(), field: True}), 2)

    def test_synthetic_provider_calls_or_errors_denied(self):
        self.assertEqual(exit_status({
            **synthetic_result(), "provider_requests_forwarded": 1}), 2)
        self.assertEqual(exit_status({
            **synthetic_result(), "errors": ["unsafe"]}), 2)

    def test_real_disposable_process_fixture(self):
        record = physical()
        self.assertEqual(exit_status(record), 0)
        self.assertFalse(record["durable_external_anchor_verified"])


if __name__ == "__main__":
    unittest.main()
