"""Denial-only contract for the physical synthetic signed-worker ledger probe."""
import unittest

from durable_worker_receipts_probe import exit_status, physical

PASS_FLAGS = (
    "unlogged_lease_debit_detected",
    "signed_commit_survives_worker_process_restart",
    "commit_pending_delivery_fences_second_commit",
    "matching_signed_delivery_survives_restart",
    "stale_anchor_replay_denied",
    "rolled_back_worker_state_mismatch_detected",
    "older_receipt_journal_denied_with_newer_anchor",
    "older_receipt_journal_with_older_anchor_still_valid",
    "wrong_worker_key_denied_on_restart",
)
FORBIDDEN = (
    "independent_worker_key_custody_verified",
    "externally_durable_anchor_verified",
    "atomic_lease_receipt_commit_verified",
    "source_authority_verified",
    "agent_dispatch_authorized",
    "model_egress_authorized",
    "used_real_repository",
)


def proof_template():
    return {
        "mode": "synthetic_restart_worker_signed_receipt_journal",
        **{key: True for key in PASS_FLAGS},
        **{key: False for key in FORBIDDEN},
        "provider_requests_forwarded": 0,
        "errors": [],
    }


class SignedWorkerReceiptPhysicalTests(unittest.TestCase):
    def test_synthetic_success_never_grants_dispatch(self):
        sample = proof_template()
        self.assertEqual(exit_status(sample), 0)
        self.assertFalse(sample["agent_dispatch_authorized"])
        self.assertEqual(sample["provider_requests_forwarded"], 0)

    def test_incomplete_proof_fails_closed(self):
        self.assertEqual(exit_status({}), 2)
        for key in PASS_FLAGS:
            with self.subTest(key=key):
                self.assertEqual(exit_status(
                    {**proof_template(), key: False}), 2)

    def test_authorization_or_external_custody_forgery_rejected(self):
        for key in FORBIDDEN:
            with self.subTest(key=key):
                self.assertEqual(exit_status(
                    {**proof_template(), key: True}), 2)

    def test_provider_forwarding_or_error_rejected(self):
        self.assertEqual(exit_status({
            **proof_template(), "provider_requests_forwarded": 1}), 2)
        self.assertEqual(exit_status({
            **proof_template(), "errors": ["missing witness"]}), 2)

    def test_real_disposable_subprocess_fixture(self):
        result = physical()
        self.assertEqual(exit_status(result), 0)
        self.assertFalse(result["atomic_lease_receipt_commit_verified"])
        self.assertFalse(result["source_authority_verified"])


if __name__ == "__main__":
    unittest.main()
