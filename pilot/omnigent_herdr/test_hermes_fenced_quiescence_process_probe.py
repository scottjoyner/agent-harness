"""Probe wrappers and replay counterexamples; all synthetic."""
from __future__ import annotations

import copy
from unittest.mock import patch
import unittest

import hermes_fenced_quiescence_process_probe as probe


class TwoProcessSyntheticFenceTests(unittest.TestCase):
    def test_default_exposes_only_hold(self):
        with patch.object(probe.subprocess, "run") as child:
            self.assertFalse(probe.gate.evaluate()["synthetic_evidence_contract_pass"])
        child.assert_not_called()

    def test_two_process_positive_and_negative_matrix(self):
        result=probe.physical()
        for k in ("fresh_pinned_revocation_pass",
                  "stale_signed_replay_rejected_against_new_head",
                  "missing_revoked_claim_rejected",
                  "rollback_of_head_and_floor_counterexample_reproduced",
                  "distinct_verifier_process"):
            self.assertTrue(result[k],(k,result))
        self.assertFalse(result["independent_authority_custody"])
        self.assertEqual(result["real_task_claims_processed"],0)
        self.assertEqual(result["provider_requests_forwarded"],0)
        self.assertFalse(result["production_rollback_authorized"])

    def test_invalid_verifier_child_fails_closed(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        s=Ed25519PrivateKey.generate()
        w="a"*64
        state=probe.sign(signer=s,claims=[],worker=w,seq=1,predecessor="0"*64)
        with patch.object(probe.subprocess,"run",side_effect=OSError("disallowed")):
            with self.assertRaises(OSError):
                probe.invoke(state,s,claims=[],sequence=1,floor=0,
                             previous="0"*64,pinned_digest=probe.gate.state_digest(state))

    def test_fake_receipt_has_no_independent_custody_bit(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        s=Ed25519PrivateKey.generate()
        out=probe.sign(signer=s,claims=[],worker="a"*64,seq=1,predecessor="0"*64)
        payload=out["payload"]
        self.assertIs(payload["independent_custody_verified"],False)
        self.assertIs(payload["side_effects_attested"],False)
        self.assertIs(payload["production_dispatch_allowed"],False)
        self.assertEqual(payload["decision"],"HOLD")


if __name__=="__main__":
    unittest.main()
