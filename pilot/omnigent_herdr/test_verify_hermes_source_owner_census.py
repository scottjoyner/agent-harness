"""Two-role synthetic source-owned claim census + revocation negative suite."""
from __future__ import annotations

import copy
import hashlib
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import verify_hermes_source_owner_census as source
import hermes_fenced_quiescence_process_probe as probe
import verify_hermes_fenced_quiescence as fence


class SyntheticSourceOwnerCensusTests(unittest.TestCase):
    def setUp(self):
        self.owner = Ed25519PrivateKey.generate()
        self.revoker = Ed25519PrivateKey.generate()
        self.worker = hashlib.sha256(b"synthetic-owner-bound-worker").hexdigest()
        self.claims = sorted([hashlib.sha256(x).hexdigest()
                              for x in (b"owner-claim-1", b"owner-claim-2")])
        self.census = self.sign_census(self.claims)
        self.revocation = probe.sign(
            signer=self.revoker, claims=self.claims, worker=self.worker,
            seq=1, predecessor="0"*64,
        )
        self.census_inputs = {
            "census": self.census,
            "trusted_source_owner_key": self.owner.public_key(),
            "expected_owner_pin": source.fingerprint(self.owner.public_key()),
            "expected_census_digest": source.digest(self.census),
            "expected_census_sequence": 1,
            "retained_census_minimum": 0,
            "expected_census_previous": "0"*64,
            "trusted_now": 110,
            "fence_args": {
                "signed_state": self.revocation,
                "trusted_authority_key": self.revoker.public_key(),
                "expected_authority_pin": fence.fingerprint(self.revoker.public_key()),
                "expected_state_digest": fence.state_digest(self.revocation),
                "expected_sequence": 1,
                "trusted_minimum_sequence": 0,
                "expected_previous_digest": "0"*64,
                "expected_worker_pin": self.worker,
                "trusted_now": 110,
                "quiescence": probe.quiescence(),
                "compose_render": probe.compose(),
                "expected_disposable_image": probe.IMAGE,
            },
        }

    def sign_census(self, claims, *, signer=None, sequence=1,
                    previous=None, worker=None):
        signer = signer or self.owner
        body = {
            "schema": 1, "kind": "synthetic_source_owner_claim_census",
            "scope": "hermes-synthetic-owner-claims",
            "source_owner_pin": source.fingerprint(signer.public_key()),
            "worker_pin": worker or self.worker,
            "sequence": sequence,
            "previous_sha256": previous if previous is not None else "0"*64,
            "issued_at": 100, "expires_at": 160,
            "active_claim_sha256": claims,
            "claim_set_sha256": source.claim_digest(claims),
            "resource_side_effects_attested": False,
            "independent_custody_verified": False,
            "source_owner_admission_authorized": False,
            "decision": "HOLD",
        }
        return {"algorithm": "Ed25519", "payload": body,
                "signature": signer.sign(source.DOMAIN+source.canonical(body)).hex()}

    def check(self, **patches):
        args = dict(self.census_inputs)
        args.update(patches)
        return source.check_source_owned_quiescence(**args)

    def reject(self, result, *, cause=None):
        self.assertFalse(result["synthetic_evidence_contract_pass"],result)
        self.assertFalse(result["production_no_dispatch_authorized"])
        self.assertFalse(result["production_rollback_authorized"])
        self.assertEqual(result["decision"],"HOLD")
        if cause:
            self.assertIn(cause,result["violations"])

    def test_fresh_owner_and_revoker_signed_complete_claims_only_synthetic_pass(self):
        r=self.check()
        self.assertTrue(r["synthetic_evidence_contract_pass"],r)
        self.assertTrue(r["synthetic_owner_census_verified"])
        self.assertTrue(r["synthetic_all_censused_claims_revoked"])
        self.assertFalse(r["source_owner_truly_independent"])
        self.assertFalse(r["external_head_independently_custodied"])
        self.assertFalse(r["resource_side_effects_attested"])
        self.assertEqual(r["decision"],"HOLD")

    def test_missing_owner_census_denies_before_revocation(self):
        with patch.object(source.fence,"evaluate") as checker:
            r=self.check(census=None)
        checker.assert_not_called()
        self.reject(r,cause="untrusted_or_stale_source_owner_claim_census")

    def test_incomplete_owner_census_vs_complete_revocation_rejected(self):
        shorter=self.sign_census(self.claims[:1])
        r=self.check(census=shorter,expected_census_digest=source.digest(shorter))
        self.assertTrue(r["synthetic_owner_census_verified"])
        self.reject(r,cause="revocation_or_quiescence_incomplete")

    def test_complete_census_vs_partial_revocation_rejected(self):
        partial=probe.sign(signer=self.revoker,claims=self.claims[:1],
                           worker=self.worker,seq=1,predecessor="0"*64)
        args=dict(self.census_inputs["fence_args"])
        args.update(signed_state=partial,expected_state_digest=fence.state_digest(partial))
        r=self.check(fence_args=args)
        self.assertTrue(r["synthetic_owner_census_verified"])
        self.reject(r,cause="revocation_or_quiescence_incomplete")

    def test_same_owner_and_revoker_key_denied_even_both_signatures_match(self):
        body=self.sign_census(self.claims,signer=self.revoker)
        r=self.check(census=body,
                     trusted_source_owner_key=self.revoker.public_key(),
                     expected_owner_pin=fence.fingerprint(self.revoker.public_key()),
                     expected_census_digest=source.digest(body))
        self.reject(r,cause="untrusted_or_stale_source_owner_claim_census")

    def test_substituted_owner_public_key_rejected(self):
        attacker=Ed25519PrivateKey.generate()
        self.reject(self.check(trusted_source_owner_key=attacker.public_key()),
                    cause="untrusted_or_stale_source_owner_claim_census")

    def test_owner_signed_snapshot_mutation_rejected_even_with_recomputed_digest(self):
        bad=copy.deepcopy(self.census)
        bad["payload"]["active_claim_sha256"]=self.claims[:1]
        bad["payload"]["claim_set_sha256"]=source.claim_digest(self.claims[:1])
        self.reject(self.check(census=bad,expected_census_digest=source.digest(bad)),
                    cause="untrusted_or_stale_source_owner_claim_census")

    def test_valid_owner_signer_replay_rejected_by_retained_newer_head(self):
        self.reject(self.check(expected_census_sequence=2,
                               retained_census_minimum=1),
                    cause="untrusted_or_stale_source_owner_claim_census")

    def test_old_signed_snapshot_with_old_head_and_floor_counterexample(self):
        # Current test has both older head and older floor; signature passes,
        # but no independent proof that the anchors weren't rolled back.
        r=self.check()
        self.assertTrue(r["synthetic_evidence_contract_pass"])
        self.assertFalse(r["external_head_independently_custodied"])

    def test_source_census_generation_skip_denied(self):
        new=self.sign_census(self.claims,sequence=3,previous="a"*64)
        self.reject(self.check(census=new,
                               expected_census_digest=source.digest(new),
                               expected_census_sequence=3,
                               retained_census_minimum=1,
                               expected_census_previous="a"*64))

    def test_source_census_wrong_previous_head_denied(self):
        self.reject(self.check(expected_census_previous="a"*64))

    def test_census_expiry_and_unauthenticated_clock_denied(self):
        for now in (99,160,True,None):
            with self.subTest(now=now):
                self.reject(self.check(trusted_now=now))

    def test_census_wrong_worker_identity_rejected(self):
        snap=self.sign_census(self.claims,worker="f"*64)
        self.reject(self.check(census=snap,expected_census_digest=source.digest(snap)))

    def test_duplicate_unsorted_and_overlarge_claim_set_denied(self):
        for claims in ([self.claims[0]]*2,
                       list(reversed(self.claims)),
                       [hashlib.sha256(str(i).encode()).hexdigest()
                        for i in range(9)]):
            snap=self.sign_census(claims)
            self.reject(self.check(census=snap,expected_census_digest=source.digest(snap)))

    def test_forged_resource_attestation_and_release_field_denied(self):
        for key in ("resource_side_effects_attested",
                    "independent_custody_verified",
                    "source_owner_admission_authorized"):
            snap=copy.deepcopy(self.census)
            snap["payload"][key]=True
            self.reject(self.check(census=snap,expected_census_digest=source.digest(snap)))

    def test_revocation_not_consulted_on_stale_owner_record(self):
        with patch.object(source.fence,"evaluate") as checker:
            r=self.check(expected_census_digest="f"*64)
        checker.assert_not_called()
        self.reject(r)

    def test_unexpected_fence_argument_and_worker_supplied_list_denied(self):
        d=dict(self.census_inputs["fence_args"])
        d["expected_revoked_claim_sha256"]=self.claims
        self.reject(self.check(fence_args=d))
        d=dict(self.census_inputs["fence_args"])
        d["release_authorized"]=True
        self.reject(self.check(fence_args=d))

    def test_missing_or_bad_payload_denied(self):
        for value in (None,True,{},{"payload":{}},
                      {"algorithm":"Ed25519","payload":{},"signature":"abc"}):
            with self.subTest(value=str(value)[:20]):
                self.reject(self.check(census=value))

    def test_no_claims_still_requires_two_distinct_signed_receipts(self):
        empty=self.sign_census([])
        empty_rev=probe.sign(signer=self.revoker,claims=[],
                             worker=self.worker,seq=1,predecessor="0"*64)
        args=dict(self.census_inputs["fence_args"])
        args.update(signed_state=empty_rev,
                    expected_state_digest=fence.state_digest(empty_rev))
        r=self.check(census=empty,expected_census_digest=source.digest(empty),
                     fence_args=args)
        self.assertTrue(r["synthetic_evidence_contract_pass"],r)
        self.assertFalse(r["production_rollback_authorized"])

    def test_report_has_no_claim_hashes_or_trust_roots(self):
        report=str(self.check())
        for prohibited in (*self.claims,self.census_inputs["expected_owner_pin"],
                           "/media/scott","FLEET_ROUTER_BEARER_TOKEN"):
            self.assertNotIn(prohibited,report)


if __name__=="__main__":
    unittest.main()
