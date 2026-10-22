"""Default deny and independent subprocess witness regression tests."""
from __future__ import annotations

from unittest.mock import patch
import unittest

import hermes_source_claim_census_process_probe as probe


class SyntheticSourceCensusProcessTests(unittest.TestCase):
    def test_default_never_starts_subprocess(self):
        with patch.object(probe.subprocess,"run") as child:
            self.assertFalse(probe.owner.check_source_owned_quiescence()[
                "synthetic_evidence_contract_pass"])
        child.assert_not_called()

    def test_two_distinct_role_signatures_and_subprocess_proof(self):
        result=probe.physical()
        self.assertTrue(result["separate_verifier_os_process"])
        self.assertTrue(result["fresh_two_role_signed_claim_set_pass"])
        self.assertTrue(result["omitted_revoked_claim_denied"])
        self.assertTrue(result["stale_owner_census_denied_with_new_head"])
        self.assertTrue(result["both_heads_rollback_counterexample_reproduced"])
        self.assertFalse(result["source_owner_independently_administered"])
        self.assertFalse(result["revocation_authority_independently_administered"])
        self.assertEqual(result["real_claims_queried_or_modified"],0)
        self.assertEqual(result["decision"],"HOLD")

    def test_child_unavailable_is_fail_closed(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        source=Ed25519PrivateKey.generate()
        revoker=Ed25519PrivateKey.generate()
        worker="a"*64
        a=probe.signed_census(source,[],worker,1,"0"*64)
        b=probe.prior.sign(signer=revoker,claims=[],worker=worker,
                           seq=1,predecessor="0"*64)
        with patch.object(probe.subprocess,"run",side_effect=OSError("isolated child unavailable")):
            with self.assertRaises(OSError):
                probe.run_child(a,b,owner_key=source,revoker_key=revoker,worker=worker,
                                seq=1,source_floor=0,source_prev="0"*64,
                                fence_floor=0,fence_prev="0"*64,
                                source_hash=probe.owner.digest(a),
                                fence_hash=probe.fence.state_digest(b))

    def test_default_label_is_not_deployment_permit(self):
        self.assertNotEqual(probe.prior.IMAGE,"assistx-hermes-adapter")
        self.assertIn("synthetic",probe.prior.IMAGE)


if __name__=="__main__":
    unittest.main()
