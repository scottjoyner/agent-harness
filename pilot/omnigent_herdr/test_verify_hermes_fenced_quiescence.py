"""Signed synthetic claim-revocation + quiescence negative controls.

All signing keys, claims, digests and times are disposable fake fixtures.
"""
from __future__ import annotations

import copy
import hashlib
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import verify_hermes_fenced_quiescence as fence
import verify_hermes_quiescent_compose as compose


IMAGE = "og-hermes-quiescent-base:abc123def456"


def compose_sample():
    return {"name": "unique-disposable-research",
            "services": {compose.SERVICE: {
                "profiles": [compose.PROFILE], "image": IMAGE,
                "pull_policy": "never", "entrypoint": ["/bin/sleep"],
                "command": ["75"], "restart": "no", "user": "1000:1000",
                "read_only": True, "network_mode": "none",
                "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
                "pids_limit": 24, "mem_limit": "134217728", "cpus": 0.5,
            }}}


def quiescent():
    return {"schema": 1, "candidate_absent": True,
            "restart_disabled": True, "claims_zero": True,
            "inflight_effects_zero": True, "provider_requests_zero": True,
            "legacy_restore_forbidden": True, "scratch_intact": True}


class FencedQuiescenceTests(unittest.TestCase):
    def setUp(self):
        self.authority = Ed25519PrivateKey.generate()
        self.owner = self.authority.public_key()
        self.pin = fence.fingerprint(self.owner)
        self.worker_pin = hashlib.sha256(b"SYNTHETIC_UNRELATED_WORKER").hexdigest()
        self.claims = sorted([
            hashlib.sha256(b"synthetic-claim-one").hexdigest(),
            hashlib.sha256(b"synthetic-claim-two").hexdigest(),
        ])
        self.state = self.make_state(self.claims)
        self.args = {
            "trusted_authority_key": self.owner,
            "expected_authority_pin": self.pin,
            "expected_state_digest": fence.state_digest(self.state),
            "expected_sequence": 1,
            "trusted_minimum_sequence": 0,
            "expected_previous_digest": "0"*64,
            "expected_worker_pin": self.worker_pin,
            "expected_revoked_claim_sha256": self.claims,
            "trusted_now": 110,
            "quiescence": quiescent(),
            "compose_render": compose_sample(),
            "expected_disposable_image": IMAGE,
        }

    def make_state(self, claims, *, sequence=1, previous=None):
        body = {
            "schema": 1, "kind": fence.KIND,
            "scope": "hermes-synthetic-no-dispatch",
            "authority_pin": self.pin,
            "worker_pin": self.worker_pin,
            "sequence": sequence,
            "previous_state_sha256": previous if previous is not None else "0"*64,
            "issued_at": 100, "expires_at": 160,
            "revoked_claim_sha256": claims,
            "claim_set_sha256": fence.claimset_digest(claims),
            "pending_claims": 0, "inflight_effects": 0,
            "provider_requests": 0,
            "side_effects_attested": False,
            "independent_custody_verified": False,
            "production_dispatch_allowed": False,
            "decision": "HOLD",
        }
        return {"algorithm": "Ed25519", "payload": body,
                "signature": self.authority.sign(
                    fence.DOMAIN + fence.canonical(body)).hex()}

    def check(self, **kwargs):
        arg = dict(self.args)
        arg.update(kwargs)
        arg.setdefault("signed_state", self.state)
        return fence.evaluate(**arg)

    def deny(self, result, reason=None):
        self.assertFalse(result["synthetic_evidence_contract_pass"], result)
        self.assertFalse(result["production_no_dispatch_authorized"])
        self.assertFalse(result["production_rollback_authorized"])
        self.assertEqual(result["decision"], "HOLD")
        if reason:
            self.assertIn(reason, result["violations"])

    def test_pinned_two_claim_quiescence_passes_synthetic_only(self):
        v=self.check()
        self.assertTrue(v["synthetic_evidence_contract_pass"],v)
        self.assertEqual(v["revoked_claim_count"],2)
        self.assertFalse(v["external_head_independently_custodied"])
        self.assertFalse(v["source_owner_revocation_verified"])
        self.assertFalse(v["real_inflight_effects_fenced"])
        self.assertFalse(v["production_no_dispatch_authorized"])
        self.assertEqual(v["decision"],"HOLD")

    def test_missing_signed_revocation_denied(self):
        self.deny(fence.evaluate(),"signed_revocation_or_retained_head_rejected")

    def test_worker_not_allowed_to_substitute_authority_key(self):
        attacker=Ed25519PrivateKey.generate()
        self.deny(self.check(trusted_authority_key=attacker.public_key()),
                  "signed_revocation_or_retained_head_rejected")

    def test_changed_signed_body_denied_even_if_digest_recomputed(self):
        forged=copy.deepcopy(self.state)
        forged["payload"]["pending_claims"]=1
        self.deny(self.check(expected_state_digest=fence.state_digest(forged),
                             signed_state=forged),
                  "signed_revocation_or_retained_head_rejected")

    def test_changed_revocation_signature_denied_even_if_digest_recomputed(self):
        forged=copy.deepcopy(self.state)
        forged["signature"]="00"*64
        self.deny(self.check(expected_state_digest=fence.state_digest(forged),
                             signed_state=forged),
                  "signed_revocation_or_retained_head_rejected")

    def test_partial_or_unknown_revoked_claim_denied(self):
        for wanted in (self.claims[:1],[],
                       sorted(self.claims+[hashlib.sha256(b"unexpected").hexdigest()])):
            with self.subTest(wanted=len(wanted)):
                self.deny(self.check(expected_revoked_claim_sha256=wanted),
                          "signed_revocation_or_retained_head_rejected")

    def test_pending_claim_or_inflight_effect_denied(self):
        for field in ("pending_claims","inflight_effects","provider_requests"):
            with self.subTest(field=field):
                bad=self.make_state(self.claims)
                bad["payload"][field]=1
                self.deny(self.check(signed_state=bad),
                          "signed_revocation_or_retained_head_rejected")

    def test_retained_state_head_and_predecessor_must_match(self):
        self.deny(self.check(expected_state_digest="f"*64),
                  "signed_revocation_or_retained_head_rejected")
        self.deny(self.check(expected_previous_digest="a"*64),
                  "signed_revocation_or_retained_head_rejected")

    def test_stale_signed_state_refused_against_retained_new_head(self):
        # The old receipt is validly signed, but not fresh enough.
        self.deny(self.check(expected_sequence=2,trusted_minimum_sequence=1),
                  "signed_revocation_or_retained_head_rejected")

    def test_rewinding_both_trust_head_and_floor_is_counterexample(self):
        # Illustrates WHY a worker-admin-held floor is not independent.
        older=self.check()
        self.assertTrue(older["synthetic_evidence_contract_pass"])
        self.assertFalse(older["external_head_independently_custodied"])

    def test_expired_receipt_denied(self):
        self.deny(self.check(trusted_now=160),
                  "signed_revocation_or_retained_head_rejected")

    def test_wrong_worker_or_authority_pin_denied(self):
        self.deny(self.check(expected_worker_pin="f"*64),
                  "signed_revocation_or_retained_head_rejected")
        self.deny(self.check(expected_authority_pin="f"*64),
                  "signed_revocation_or_retained_head_rejected")

    def test_no_claims_must_still_be_signed(self):
        empty=self.make_state([])
        out=self.check(signed_state=empty,
                       expected_state_digest=fence.state_digest(empty),
                       expected_revoked_claim_sha256=[])
        self.assertTrue(out["synthetic_evidence_contract_pass"])
        self.assertFalse(out["production_no_dispatch_authorized"])

    def test_missing_stop_remove_or_legacy_restore_denied(self):
        for key in ("candidate_absent","restart_disabled","claims_zero",
                    "inflight_effects_zero","provider_requests_zero",
                    "legacy_restore_forbidden","scratch_intact"):
            with self.subTest(key=key):
                q=quiescent();q[key]=False
                self.deny(self.check(quiescence=q),
                          "no_dispatch_quiescence_not_proven")

    def test_real_hermes_command_or_mount_denied(self):
        for key,value in (("entrypoint",["/entrypoint.sh"]),
                          ("volumes",["/nas:/nas"]),
                          ("restart","unless-stopped"),
                          ("network_mode","host")):
            with self.subTest(key=key):
                d=compose_sample();d["services"][compose.SERVICE][key]=value
                self.deny(self.check(compose_render=d),
                          "quiescent_compose_not_qualified")

    def test_missing_compose_qualification_denied(self):
        self.deny(self.check(compose_render={}),
                  "quiescent_compose_not_qualified")
        self.deny(self.check(expected_disposable_image="hermes:latest"),
                  "quiescent_compose_not_qualified")

    def test_boolean_spoofing_and_unexpected_fields_denied(self):
        q=quiescent();q["claims_zero"]=1
        self.deny(self.check(quiescence=q),
                  "no_dispatch_quiescence_not_proven")
        v=copy.deepcopy(self.state);v["payload"]["independent_custody_verified"]=True
        self.deny(self.check(signed_state=v),
                  "signed_revocation_or_retained_head_rejected")

    def test_sequence_skip_is_denied_even_with_matching_signed_body(self):
        old=self.make_state(self.claims,sequence=3,previous="a"*64)
        self.deny(self.check(signed_state=old,
                             expected_state_digest=fence.state_digest(old),
                             expected_sequence=3,
                             trusted_minimum_sequence=1,
                             expected_previous_digest="a"*64),
                  "signed_revocation_or_retained_head_rejected")

    def test_reused_genesis_previous_digest_is_denied(self):
        forged=self.make_state(self.claims,sequence=2,previous="0"*64)
        self.deny(self.check(signed_state=forged,
                             expected_state_digest=fence.state_digest(forged),
                             expected_sequence=2,trusted_minimum_sequence=1),
                  "signed_revocation_or_retained_head_rejected")

    def test_report_never_includes_sensitive_claims(self):
        r=str(self.check())
        for secret in (*self.claims,"/media/scott","SYNTHETIC_UNRELATED_WORKER"):
            self.assertNotIn(secret,r)


if __name__=="__main__":
    unittest.main()
