"""Causal link must pin the exact owner snapshot and later revocation."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import verify_hermes_causal_claim_fence as causal
import verify_hermes_fenced_quiescence as fence
import verify_hermes_source_owner_census as owner
import test_verify_hermes_source_owner_census as fixtures


class CausalFenceResearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.SyntheticSourceOwnerCensusTests()
        self.fixture.setUp()
        f=self.fixture
        self.args=dict(f.census_inputs)
        self.args["fence_args"]=dict(f.census_inputs["fence_args"])
        # Retain Ed25519 key instances explicitly: deepcopy need not
        # preserve cryptography private/public key instances on all versions.
        self.args["trusted_source_owner_key"]=f.owner.public_key()
        self.args["fence_args"]["trusted_authority_key"]=f.revoker.public_key()
        self.args["trusted_now"]=140
        self.args["fence_args"]["trusted_now"]=140
        self.reissue_revocation(120,180)
        self.binding=self.make_binding()
        self.check_args={
            "source_args":self.args,
            "signed_binding":self.binding,
            "expected_binding_digest":causal.digest(self.binding),
            "expected_binding_sequence":1,
            "trusted_binding_minimum":0,
            "expected_previous_binding_digest":"0"*64,
            "trusted_now":140,
        }

    def reissue_revocation(self,issued,expires):
        receipt=copy.deepcopy(self.args["fence_args"]["signed_state"])
        body=receipt["payload"]
        body["issued_at"]=issued
        body["expires_at"]=expires
        receipt["signature"]=self.fixture.revoker.sign(
            fence.DOMAIN+fence.canonical(body)).hex()
        self.args["fence_args"]["signed_state"]=receipt
        self.args["fence_args"]["expected_state_digest"]=fence.state_digest(receipt)

    def make_binding(self):
        src=self.args["census"]
        rev=self.args["fence_args"]["signed_state"]
        p={
            "schema":1,"kind":causal.KIND,
            "scope":"hermes-synthetic-owner-revocation",
            "revocation_authority_pin":self.args["fence_args"]["expected_authority_pin"],
            "worker_pin":self.fixture.worker,
            "source_census_sha256":owner.digest(src),
            "source_census_sequence":src["payload"]["sequence"],
            "revocation_sha256":fence.state_digest(rev),
            "revocation_sequence":rev["payload"]["sequence"],
            "sequence":1,"previous_binding_sha256":"0"*64,
            "issued_at":130,"expires_at":170,
            "resource_effects_fenced":False,
            "independent_custody_verified":False,
            "production_dispatch_authorized":False,
            "decision":"HOLD",
        }
        return {
            "algorithm":"Ed25519","payload":p,
            "signature":self.fixture.revoker.sign(
                causal.DOMAIN+causal.canonical(p)).hex(),
        }

    def check(self, **modifications):
        args=dict(self.check_args)
        args.update(modifications)
        return causal.check(**args)

    def reject(self, result, reason=None):
        self.assertFalse(result["synthetic_evidence_contract_pass"], result)
        self.assertFalse(result["production_no_dispatch_authorized"])
        self.assertFalse(result["production_rollback_authorized"])
        self.assertEqual(result["decision"],"HOLD")
        if reason:
            self.assertIn(reason,result["violations"])

    def test_signed_owner_snapshot_and_later_revocation_causally_linked(self):
        r=self.check()
        self.assertTrue(r["synthetic_evidence_contract_pass"],r)
        self.assertTrue(r["synthetic_source_claims_verified"])
        self.assertTrue(r["synthetic_causal_link_verified"])
        self.assertFalse(r["external_head_independently_custodied"])
        self.assertFalse(r["resource_effects_fenced"])

    def test_missing_binding_rejected_even_if_claim_sets_match(self):
        self.reject(self.check(signed_binding=None), "signed_causal_binding_denied")

    def test_owner_and_revocation_matching_claims_but_revocation_earlier_rejected(self):
        self.reissue_revocation(90,150)
        self.binding=self.make_binding()
        self.reject(self.check(source_args=self.args,
                               signed_binding=self.binding,
                               expected_binding_digest=causal.digest(self.binding)),
                    "signed_causal_binding_denied")

    def test_future_owner_census_with_same_claims_cannot_reuse_old_revocation(self):
        newer=self.fixture.sign_census(self.fixture.claims)
        newer["payload"]["issued_at"]=125
        newer["payload"]["expires_at"]=170
        newer["signature"]=self.fixture.owner.sign(
            owner.DOMAIN+owner.canonical(newer["payload"])).hex()
        args=dict(self.args)
        args["census"]=newer
        args["expected_census_digest"]=owner.digest(newer)
        new_binding=self.make_binding()
        self.reject(self.check(source_args=args,
                               signed_binding=new_binding,
                               expected_binding_digest=causal.digest(new_binding)),
                    "signed_causal_binding_denied")

    def test_swapped_signed_source_digest_rejected(self):
        b=copy.deepcopy(self.binding)
        b["payload"]["source_census_sha256"]="f"*64
        b["signature"]=self.fixture.revoker.sign(
            causal.DOMAIN+causal.canonical(b["payload"])).hex()
        self.reject(self.check(signed_binding=b,
                               expected_binding_digest=causal.digest(b)),
                    "signed_causal_binding_denied")

    def test_swapped_signed_revocation_digest_rejected(self):
        b=copy.deepcopy(self.binding)
        b["payload"]["revocation_sha256"]="f"*64
        b["signature"]=self.fixture.revoker.sign(
            causal.DOMAIN+causal.canonical(b["payload"])).hex()
        self.reject(self.check(signed_binding=b,
                               expected_binding_digest=causal.digest(b)),
                    "signed_causal_binding_denied")

    def test_revocation_signing_key_substitution_denied(self):
        b=copy.deepcopy(self.binding)
        b["signature"]=self.fixture.owner.sign(
            causal.DOMAIN+causal.canonical(b["payload"])).hex()
        self.reject(self.check(signed_binding=b,
                               expected_binding_digest=causal.digest(b)),
                    "signed_causal_binding_denied")

    def test_binding_modification_and_signature_tamper_denied(self):
        b=copy.deepcopy(self.binding)
        b["payload"]["issued_at"]=129
        self.reject(self.check(signed_binding=b,
                               expected_binding_digest=causal.digest(b)),
                    "signed_causal_binding_denied")
        b=copy.deepcopy(self.binding)
        b["signature"]="00"*64
        self.reject(self.check(signed_binding=b,
                               expected_binding_digest=causal.digest(b)),
                    "signed_causal_binding_denied")

    def test_missing_or_rewound_external_binding_head_denied(self):
        self.reject(self.check(expected_binding_digest="f"*64),
                    "signed_causal_binding_denied")
        self.reject(self.check(expected_binding_sequence=2,
                               trusted_binding_minimum=1),
                    "signed_causal_binding_denied")
        self.reject(self.check(expected_previous_binding_digest="f"*64),
                    "signed_causal_binding_denied")

    def test_skipped_binding_sequence_denied(self):
        b=copy.deepcopy(self.binding)
        b["payload"].update(sequence=3,previous_binding_sha256="a"*64)
        b["signature"]=self.fixture.revoker.sign(
            causal.DOMAIN+causal.canonical(b["payload"])).hex()
        self.reject(self.check(signed_binding=b,
                               expected_binding_digest=causal.digest(b),
                               expected_binding_sequence=3,
                               trusted_binding_minimum=1,
                               expected_previous_binding_digest="a"*64),
                    "signed_causal_binding_denied")

    def test_same_snapshot_link_with_invalid_owner_census_denied_before_binding(self):
        args=dict(self.args)
        args["expected_census_digest"]="a"*64
        self.reject(self.check(source_args=args),
                    "signed_owner_claims_or_revocation_denied")

    def test_missing_revoked_claim_denied_despite_binding(self):
        partial=dict(self.args)
        partial["fence_args"]=dict(self.args["fence_args"])
        partial["fence_args"]["expected_state_digest"]="f"*64
        self.reject(self.check(source_args=partial),
                    "signed_owner_claims_or_revocation_denied")

    def test_forged_binding_custody_and_effect_flag_denied(self):
        for field in ("resource_effects_fenced","independent_custody_verified",
                      "production_dispatch_authorized"):
            b=copy.deepcopy(self.binding)
            b["payload"][field]=True
            b["signature"]=self.fixture.revoker.sign(
                causal.DOMAIN+causal.canonical(b["payload"])).hex()
            self.reject(self.check(signed_binding=b,
                                   expected_binding_digest=causal.digest(b)),
                        "signed_causal_binding_denied")

    def test_binding_clock_after_deadline_or_before_revocation_denied(self):
        for issued in (100,119,180):
            b=copy.deepcopy(self.binding)
            b["payload"]["issued_at"]=issued
            b["payload"]["expires_at"]=issued+40
            b["signature"]=self.fixture.revoker.sign(
                causal.DOMAIN+causal.canonical(b["payload"])).hex()
            self.reject(self.check(signed_binding=b,
                                   expected_binding_digest=causal.digest(b)),
                        "signed_causal_binding_denied")

    def test_forged_inputs_and_clock_denied(self):
        self.reject(causal.check(), "source_and_revocation_inputs_untrusted")
        self.reject(self.check(trusted_now=150),
                    "source_and_revocation_inputs_untrusted")
        args=dict(self.args)
        args["expected_revoked_claim_sha256"]=self.fixture.claims
        self.reject(self.check(source_args=args),
                    "source_and_revocation_inputs_untrusted")

    def test_valid_receipts_with_all_trusted_heads_rolled_back_reaccepted(self):
        # The signer and verifier cannot detect jointly rewound anchors.
        r=self.check()
        self.assertTrue(r["synthetic_evidence_contract_pass"])
        self.assertFalse(r["external_head_independently_custodied"])
        self.assertFalse(r["signer_timestamp_independent"])

    def test_output_avoids_claims_and_source_credentials(self):
        out=str(self.check())
        for secret in (*self.fixture.claims,"/media/scott","FLEET_ROUTER_BEARER_TOKEN"):
            self.assertNotIn(secret,out)


if __name__=="__main__":
    unittest.main()
