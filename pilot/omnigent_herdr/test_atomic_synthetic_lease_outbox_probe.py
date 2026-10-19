"""Deny-only qualification for the V2 single-database atomic outbox fixture."""
import unittest

from atomic_synthetic_lease_outbox_probe import exit_status, physical


PASS_FIELDS = (
    "v2_legacy_schema_separation_verified",
    "pre_insert_failure_left_no_debit",
    "post_insert_failure_rolled_back_both",
    "committed_debit_and_authenticated_intent",
    "restart_outbox_intent_persisted",
    "duplicate_operation_rejected",
    "second_receipt_chained_in_one_db",
    "old_lease_image_rejected_with_newer_head",
    "old_image_with_old_head_still_valid",
)
FAIL_FLAGS = (
    "signing_key_saved_to_disk",
    "independent_external_highwater_verified",
    "observer_publication_atomic_verified",
    "real_resource_authority_verified",
    "agent_dispatch_authorized",
    "model_egress_authorized",
    "used_real_repository",
)


def compliant_fixture():
    return {
        "mode": "synthetic_single_sqlite_atomic_lease_outbox",
        **{flag: True for flag in PASS_FIELDS},
        **{flag: False for flag in FAIL_FLAGS},
        "provider_requests_forwarded": 0,
        "errors": [],
    }


class AtomicOutboxFixtureSafetyTests(unittest.TestCase):
    def test_fixture_pass_is_not_agent_admission(self):
        sample = compliant_fixture()
        self.assertEqual(exit_status(sample), 0)
        self.assertFalse(sample["agent_dispatch_authorized"])

    def test_missing_or_negative_evidence_denied(self):
        self.assertEqual(exit_status({}), 2)
        for field in PASS_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(exit_status(
                    {**compliant_fixture(), field: False}), 2)

    def test_forged_authority_claim_denied(self):
        for field in FAIL_FLAGS:
            with self.subTest(field=field):
                self.assertEqual(exit_status(
                    {**compliant_fixture(), field: True}), 2)

    def test_provider_forwarding_or_errors_denied(self):
        self.assertEqual(exit_status({
            **compliant_fixture(), "provider_requests_forwarded": 1}), 2)
        self.assertEqual(exit_status({
            **compliant_fixture(), "errors": ["unresolved"]}), 2)

    def test_one_local_disposable_fixture(self):
        result = physical()
        self.assertEqual(exit_status(result), 0)
        self.assertFalse(result["observer_publication_atomic_verified"])
        self.assertFalse(result["real_resource_authority_verified"])


if __name__ == "__main__":
    unittest.main()
