"""Fifth-role offline authority state: trust pins and high-water are explicit.

All keys live in ephemeral *same-operator* test context. Green tests DO NOT
establish immutable or independently administered authorization. In fact,
the old-state + old-trust-head acceptance is a mandatory negative control.
"""
import unittest
from copy import deepcopy

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from observer_checkpoint import ZERO, public_fingerprint
import test_v2_custody_checkpoint_contract as custody_fixtures
from v2_external_authority_state import (
    UnsafeExternalAuthority, digest,
    sign_synthetic_state, verify_external_state,
    check_checkpoint_evidence_only, verify_revocation_evidence_only,
)

SCOPE = "herdr-omnigent.synthetic.v2"
STAMP = 1700000050


class ExternalStateTests(unittest.TestCase):
    def setUp(self):
        fixture = custody_fixtures.CustodyCheckpointTests(
            methodName="test_exact_detached_signature_requires_four_role_pins")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fx = fixture
        self.authority = Ed25519PrivateKey.generate()
        self.authority_pin = public_fingerprint(self.authority.public_key())
        self.sign_args = dict(
            authority_signer=self.authority,
            scope=SCOPE,
            custodian_pin=fixture.custodian_pin,
            witness_pin=fixture.witness_pin,
            observer_pin=fixture.observer_pin,
            worker_pin=fixture.worker_pin,
            sequence=1, previous_state_sha256=ZERO,
            checkpoint_generation=1,
            checkpoint_sha256=fixture.verify_args[
                "expected_checkpoint_sha256"],
            witness_count=fixture.witness_head[0],
            state="checkpoint_recorded",
            issued_at=1700000000, expires_at=1700000600,
        )
        self.state1 = sign_synthetic_state(**self.sign_args)
        self.state1_digest = digest(self.state1)
        self.verification = dict(
            trusted_authority_key=self.authority.public_key(),
            expected_authority_pin=self.authority_pin,
            expected_state_sha256=self.state1_digest,
            expected_sequence=1, expected_previous_sha256=ZERO,
            minimum_sequence=0, expected_scope=SCOPE,
            expected_custodian_pin=fixture.custodian_pin,
            expected_witness_pin=fixture.witness_pin,
            expected_observer_pin=fixture.observer_pin,
            expected_worker_pin=fixture.worker_pin, trusted_now=STAMP,
        )
        self.checkpoint_verify = dict(
            trusted_authority_key=self.authority.public_key(),
            expected_authority_pin=self.authority_pin,
            expected_state_sha256=self.state1_digest,
            expected_sequence=1, expected_previous_sha256=ZERO,
            minimum_sequence=0,
            checkpoint=fixture.checkpoint,
            witness_receipt=fixture.witness_receipt,
            trusted_custodian_key=fixture.custodian.public_key(),
            trusted_witness_key=fixture.witness.public_key(),
            observer_public_key=fixture.observer.public_key(),
            worker_fingerprint=fixture.worker_pin,
            observer_id="observer-test", worker_id="worker-test",
            expected_witness_head=fixture.witness_head,
            expected_observer_head=fixture.obs_head,
            expected_scope=SCOPE,
            expected_checkpoint_generation=1,
            expected_checkpoint_previous_sha256=ZERO,
            expected_checkpoint_sha256=
                fixture.verify_args["expected_checkpoint_sha256"],
            minimum_witness_count=0, trusted_now=STAMP,
        )

    _UNSET = object()

    def external(self, state=_UNSET, **changes):
        return verify_external_state(
            self.state1 if state is self._UNSET else state,
            **{**self.verification, **changes})

    def checkpoint(self, state=_UNSET, **changes):
        return check_checkpoint_evidence_only(
            authority_state=self.state1 if state is self._UNSET else state,
            **{**self.checkpoint_verify, **changes})

    def revoke(self):
        return sign_synthetic_state(**{
            **self.sign_args,
            "sequence": 2,
            "previous_state_sha256": self.state1_digest,
            "state": "checkpoint_revoked",
        })

    def test_offline_signature_and_exact_recording(self):
        self.assertTrue(self.external())
        self.assertTrue(self.checkpoint())
        self.assertFalse(self.state1["payload"]["agent_dispatch_authorized"])
        self.assertFalse(self.state1["payload"]["independent_custody_verified"])
        self.assertEqual(len({
            self.authority_pin, self.fx.custodian_pin,
            self.fx.witness_pin, self.fx.observer_pin,
            self.fx.worker_pin}), 5)

    def test_missing_external_pins_and_digest_denied(self):
        for name in ("expected_authority_pin", "expected_state_sha256",
                     "expected_previous_sha256"):
            for value in (None, "", "f"*64):
                with self.subTest(name=name, value=value):
                    self.assertFalse(self.external(**{name: value}))

    def test_replaced_authority_private_key_denied(self):
        self.assertFalse(self.external(
            trusted_authority_key=Ed25519PrivateKey.generate().public_key()))

    def test_mismatched_authority_public_pin_denied(self):
        self.assertFalse(self.external(expected_authority_pin=self.fx.witness_pin))

    def test_missing_external_counter_denied(self):
        self.assertFalse(self.external(minimum_sequence=None))
        self.assertFalse(self.external(expected_sequence=None))
        self.assertFalse(self.external(minimum_sequence=True))
        self.assertFalse(self.external(expected_sequence=True))

    def test_freshness_floor_denies_old_state(self):
        self.assertFalse(self.external(minimum_sequence=1))
        self.assertFalse(self.checkpoint(minimum_sequence=1))

    def test_stale_counter_or_predecessor_denied(self):
        self.assertFalse(self.external(expected_sequence=2))
        self.assertFalse(self.external(expected_previous_sha256="f"*64))

    def test_policy_clock_fails_closed(self):
        for instant in (1699999999, 1700000600, True, None):
            with self.subTest(clock=instant):
                self.assertFalse(self.external(trusted_now=instant))

    def test_signed_role_substitution_denied(self):
        for field in ("expected_custodian_pin", "expected_witness_pin",
                      "expected_observer_pin", "expected_worker_pin"):
            with self.subTest(field=field):
                self.assertFalse(self.external(**{field:"f"*64}))

    def test_forged_flags_and_model_authority_rejected(self):
        for k, v in (
            ("independent_custody_verified", True),
            ("source_transaction_attested", True),
            ("agent_dispatch_authorized", True),
            ("provider_requests_forwarded", 1),
            ("decision", "GO"),
        ):
            with self.subTest(field=k):
                bad=deepcopy(self.state1)
                bad["payload"][k]=v
                self.assertFalse(self.external(bad))

    def test_invalid_or_duplicate_shape_rejected(self):
        for value in ({}, [], None, "bad"):
            with self.subTest(type=str(type(value))):
                self.assertFalse(self.external(value))
        bad=deepcopy(self.state1)
        bad["payload"]["debug"]="false"
        self.assertFalse(self.external(bad))

    def test_mutated_payload_or_signature_denied(self):
        bad=deepcopy(self.state1)
        bad["payload"]["scope"]="other"
        self.assertFalse(self.external(bad))
        bad=deepcopy(self.state1)
        bad["signature"]="AAAA"+bad["signature"][4:]
        self.assertFalse(self.external(bad))

    def test_signed_revocation_proves_a_deny_only_tombstone(self):
        revoked=self.revoke()
        revoked_sha=digest(revoked)
        args={
            **self.verification,
            "expected_state_sha256": revoked_sha,
            "expected_sequence": 2,
            "expected_previous_sha256": self.state1_digest,
            "minimum_sequence": 1,
        }
        self.assertTrue(verify_external_state(revoked, **args))
        self.assertTrue(verify_revocation_evidence_only(
            authority_state=revoked,
            **args,
            expected_checkpoint_sha256=
                self.fx.verify_args["expected_checkpoint_sha256"]))
        self.assertFalse(self.checkpoint(
            revoked, expected_state_sha256=revoked_sha,
            expected_sequence=2,
            expected_previous_sha256=self.state1_digest,
            minimum_sequence=1))

    def test_revocation_untrusted_key_or_wrong_checkpoint_denied(self):
        revoked=self.revoke()
        common={
            **self.verification,
            "expected_state_sha256": digest(revoked),
            "expected_sequence": 2,
            "expected_previous_sha256": self.state1_digest,
            "minimum_sequence": 1,
            "expected_checkpoint_sha256": "f"*64,
        }
        self.assertFalse(verify_revocation_evidence_only(
            authority_state=revoked, **common))
        self.assertFalse(verify_revocation_evidence_only(
            authority_state=revoked,
            **{**common,
               "expected_checkpoint_sha256":
                   self.fx.verify_args["expected_checkpoint_sha256"],
               "trusted_authority_key":
                   Ed25519PrivateKey.generate().public_key()}))

    def test_a_revocation_cannot_be_used_as_positive_checkpoint(self):
        revoked=self.revoke()
        self.assertFalse(self.checkpoint(
            revoked,
            expected_state_sha256=digest(revoked),
            expected_sequence=2,
            expected_previous_sha256=self.state1_digest,
            minimum_sequence=1))

    def test_signed_authority_state_binds_exact_custody_receipt(self):
        forged=deepcopy(self.fx.checkpoint)
        forged["payload"]["observer_revision"]=3
        self.assertFalse(self.checkpoint(checkpoint=forged))
        self.assertFalse(self.checkpoint(
            expected_checkpoint_sha256="f"*64))
        self.assertFalse(self.checkpoint(
            expected_checkpoint_generation=2))
        self.assertFalse(self.checkpoint(
            minimum_witness_count=1))
        self.assertFalse(self.checkpoint(
            witness_receipt={"forged": "receipt"}))

    def test_expired_authority_even_with_valid_checkpoint_refused(self):
        self.assertFalse(self.checkpoint(trusted_now=1700000600))

    def test_all_roles_five_way_unique(self):
        with self.assertRaises(UnsafeExternalAuthority):
            sign_synthetic_state(**{
                **self.sign_args,
                "authority_signer":self.fx.custodian,
            })

    def test_sequence_must_chain_nonzero_after_initial(self):
        for previous in (ZERO, None, ""):
            with self.subTest(previous=previous):
                with self.assertRaises(UnsafeExternalAuthority):
                    sign_synthetic_state(**{
                        **self.sign_args, "sequence":2,
                        "previous_state_sha256": previous})

    def test_expiration_and_generation_limits(self):
        for changes in (
            {"expires_at":1700007200},
            {"issued_at":True},
            {"sequence":17},
            {"checkpoint_generation":5},
            {"witness_count":5},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(UnsafeExternalAuthority):
                    sign_synthetic_state(**{**self.sign_args, **changes})

    def test_restored_ledger_denied_with_retained_new_revocation(self):
        revoked=self.revoke()
        self.assertTrue(verify_revocation_evidence_only(
            authority_state=revoked, trusted_authority_key=
                self.authority.public_key(),
            expected_authority_pin=self.authority_pin,
            expected_state_sha256=digest(revoked), expected_sequence=2,
            expected_previous_sha256=self.state1_digest,
            minimum_sequence=1, expected_scope=SCOPE,
            expected_custodian_pin=self.fx.custodian_pin,
            expected_witness_pin=self.fx.witness_pin,
            expected_observer_pin=self.fx.observer_pin,
            expected_worker_pin=self.fx.worker_pin,
            expected_checkpoint_sha256=
                self.fx.verify_args["expected_checkpoint_sha256"],
            trusted_now=STAMP))
        # A valid old signed checkpoint is blocked by the pinned NEW state.
        self.assertFalse(self.checkpoint(
            revoked, expected_state_sha256=digest(revoked),
            expected_sequence=2,
            expected_previous_sha256=self.state1_digest,
            minimum_sequence=1))

    def test_old_state_with_restored_old_expected_trust_inputs_is_attack(self):
        revoked=self.revoke()
        self.assertFalse(self.checkpoint(
            revoked, expected_state_sha256=digest(revoked),
            expected_sequence=2,
            expected_previous_sha256=self.state1_digest,
            minimum_sequence=1))
        # Counterexample: same admin resets "expected" state & floor too.
        # Signature remains true but independent custody has NOT been proven.
        self.assertTrue(self.checkpoint(
            self.state1, expected_state_sha256=self.state1_digest,
            expected_sequence=1, expected_previous_sha256=ZERO,
            minimum_sequence=0))
        self.assertFalse(self.fx.checkpoint["payload"][
            "independent_custody_verified"])

    def test_returned_success_never_has_a_dispatch_capability(self):
        self.assertTrue(self.checkpoint())
        self.assertFalse(self.state1["payload"]["agent_dispatch_authorized"])
        self.assertFalse(self.fx.checkpoint["payload"]["agent_dispatch_authorized"])


if __name__ == "__main__":
    unittest.main()
