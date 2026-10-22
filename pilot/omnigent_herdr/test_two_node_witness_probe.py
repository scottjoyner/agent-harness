"""Fail-closed output contract for cross-node cooperative witness fixture."""
import unittest

from two_node_witness_probe import exit_status, physical

FIELDS = (
    "two_signed_checkpoints_published",
    "worker_old_state_rejected_against_newer_head",
    "truncated_witness_rejected_with_newer_anchor",
    "stale_writer_rejected",
    "old_log_and_old_anchor_still_match",
    "remote_fixture_exited_cleanly",
)
FORBIDDEN = (
    "signer_key_sent_to_worker", "worker_key_sent_to_observer",
    "independent_anchor_custody_verified", "source_attestation_verified",
    "agent_dispatch_authorized", "used_real_repository",
)


def synthetic_proof():
    return {
        "schema": 1,
        "mode": "two_node_cooperative_witness_log",
        **{key: True for key in FIELDS},
        **{key: False for key in FORBIDDEN},
        "provider_requests_forwarded": 0, "errors": [],
    }


class TwoNodeWitnessTests(unittest.TestCase):
    def test_complete_fixture_still_denies_live_agents(self):
        report = synthetic_proof()
        self.assertEqual(exit_status(report), 0)
        self.assertFalse(report["agent_dispatch_authorized"])
        self.assertEqual(report["provider_requests_forwarded"], 0)

    def test_missing_evidence_is_fail_closed(self):
        self.assertEqual(exit_status({}), 2)
        for name in FIELDS:
            with self.subTest(name=name):
                self.assertEqual(exit_status({
                    **synthetic_proof(), name: False,
                }), 2)

    def test_forged_ownership_or_egress_claims_fail(self):
        for name in FORBIDDEN:
            with self.subTest(name=name):
                self.assertEqual(exit_status({
                    **synthetic_proof(), name: True,
                }), 2)
        self.assertEqual(exit_status({
            **synthetic_proof(), "provider_requests_forwarded": 1,
        }), 2)

    def test_remote_target_explicitly_allowlisted(self):
        report = physical(remote_host="production")
        self.assertEqual(exit_status(report), 2)
        self.assertEqual(report["errors"], ["unapproved remote fixture node"])


if __name__ == "__main__":
    unittest.main()
