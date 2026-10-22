"""Safety properties of the disposable observer-journal process fixture."""
import unittest

from durable_observer_journal_probe import exit_status, physical


def compliant_fixture():
    return {
        "schema": 1,
        "mode": "synthetic_restart_durable_observer_journal",
        "prepare_fence_survived_restart": True,
        "unmodified_prepare_can_abort": True,
        "signed_unconfirmed_ack_survived_restart": True,
        "tampered_ack_cannot_release_fence": True,
        "correct_ack_can_confirm_after_restart": True,
        "postconfirm_allows_next_prepare": True,
        "restored_old_observer_db_vulnerability_reproduced": True,
        "signing_key_on_disk": False,
        "independent_signer_custody_verified": False,
        "external_highwater_verified": False,
        "agent_dispatch_authorized": False,
        "model_egress_authorized": False,
        "provider_requests_forwarded": 0,
        "used_real_repository": False,
        "errors": [],
    }


class JournalProbeTests(unittest.TestCase):
    def test_synthetic_fixture_has_no_production_authorization(self):
        self.assertEqual(exit_status(compliant_fixture()), 0)

    def test_missing_fixture_evidence_denied(self):
        self.assertEqual(exit_status({}), 2)

    def test_forged_independence_or_dispatch_denied(self):
        for field in (
            "signing_key_on_disk", "independent_signer_custody_verified",
            "external_highwater_verified", "agent_dispatch_authorized",
            "model_egress_authorized", "used_real_repository",
        ):
            with self.subTest(field=field):
                self.assertEqual(exit_status({
                    **compliant_fixture(), field: True,
                }), 2)

    def test_unproven_restart_or_negative_rollback_denied(self):
        for field in (
            "prepare_fence_survived_restart",
            "signed_unconfirmed_ack_survived_restart",
            "tampered_ack_cannot_release_fence",
            "restored_old_observer_db_vulnerability_reproduced",
        ):
            with self.subTest(field=field):
                self.assertEqual(exit_status({
                    **compliant_fixture(), field: False,
                }), 2)

    def test_local_disposable_fixture_passes(self):
        result = physical()
        self.assertEqual(exit_status(result), 0)
        self.assertFalse(result["agent_dispatch_authorized"])


if __name__ == "__main__":
    unittest.main()
