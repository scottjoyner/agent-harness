import unittest

from observer_replay_probe import exit_status, physical


class ObserverPhysicalProbeTests(unittest.TestCase):
    def test_absent_evidence_fails_closed(self):
        self.assertEqual(exit_status({}), 2)

    def test_proof_must_not_claim_real_custody_or_dispatch(self):
        result = {
            "signed_latest_state_matches": True,
            "restored_old_db_rejected_with_latest_checkpoint": True,
            "stale_checkpoint_rejected_with_trusted_floor": True,
            "old_checkpoint_with_old_floor_still_matches": True,
            "altered_signature_rejected": True,
            "observer_public_key_pin_enforced": True,
            "observer_private_key_persisted": False,
            "physical_observer_custody_verified": False,
            "continuous_publication_verified": False,
            "rollback_resistance_verified": False,
            "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0,
            "errors": [],
        }
        self.assertEqual(exit_status(result), 0)
        for key in (
            "observer_private_key_persisted",
            "physical_observer_custody_verified",
            "continuous_publication_verified",
            "rollback_resistance_verified",
            "agent_dispatch_authorized",
        ):
            changed = {**result, key: True}
            self.assertEqual(exit_status(changed), 2)

    def test_local_fixture_completes_without_external_models(self):
        result = physical()
        self.assertEqual(exit_status(result), 0)
        self.assertFalse(result["agent_dispatch_authorized"])
        self.assertEqual(result["provider_requests_forwarded"], 0)


if __name__ == "__main__":
    unittest.main()
