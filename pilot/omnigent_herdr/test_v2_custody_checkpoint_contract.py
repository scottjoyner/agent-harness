"""Strict detached external-root syntax tests; all custody is SYNTHETIC."""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from observer_checkpoint import public_fingerprint
from v2_durable_observer_inbox import SyntheticV2DurableObserverInbox
from v2_offhost_marker_witness import V2OffhostMarkerWitness
from v2_witness_countersign import countersign
from v2_custody_checkpoint_contract import (
    UnsafeCustodyCheckpoint, checkpoint_digest,
    sign_research_checkpoint, verify_research_checkpoint,
)

BEFORE = hashlib.sha256(b"detached-v2-test-before").hexdigest()
ZERO = "0" * 64
SCOPE = "herdr-omnigent.synthetic.v2"


class CustodyCheckpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="og-v2-external-custody-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        os.chmod(self.root, 0o700)
        self.worker = Ed25519PrivateKey.generate()
        self.observer = Ed25519PrivateKey.generate()
        self.witness = Ed25519PrivateKey.generate()
        self.custodian = Ed25519PrivateKey.generate()
        self.worker_pin = public_fingerprint(self.worker.public_key())
        self.observer_pin = public_fingerprint(self.observer.public_key())
        self.witness_pin = public_fingerprint(self.witness.public_key())
        self.custodian_pin = public_fingerprint(self.custodian.public_key())
        self.inbox = SyntheticV2DurableObserverInbox(
            self.root / "observer.sqlite3",
            observer_signer=self.observer,
            worker_public_key=self.worker.public_key(),
            observer_id="observer-test", worker_id="worker-test",
            initial_state=BEFORE, create=True)
        self.obs_head = self.inbox.created_head
        self.witness_store = V2OffhostMarkerWitness(
            self.root / "witness.jsonl",
            observer_public_key=self.observer.public_key(),
            observer_id="observer-test", worker_id="worker-test",
            worker_pin=self.worker_pin)
        self.witness_head = self.witness_store.append(
            self.inbox.export_signed_marker(trusted_anchor=self.obs_head),
            trusted_anchor=None, genesis=True)
        self.witness_receipt = countersign(
            self.witness_store, signer=self.witness,
            witness_head=self.witness_head, observer_head=self.obs_head)
        self.sign_args = dict(
            custodian_signer=self.custodian,
            witness_receipt=self.witness_receipt,
            trusted_witness_key=self.witness.public_key(),
            expected_witness_pin=self.witness_pin,
            observer_public_key=self.observer.public_key(),
            worker_fingerprint=self.worker_pin,
            observer_id="observer-test",
            worker_id="worker-test",
            expected_witness_head=self.witness_head,
            expected_observer_head=self.obs_head,
            scope=SCOPE, generation=1,
            previous_checkpoint_sha256=ZERO,
            issued_at=1700000000, expires_at=1700000600)
        self.checkpoint = sign_research_checkpoint(**self.sign_args)
        self.verify_args = dict(
            trusted_custodian_key=self.custodian.public_key(),
            expected_custodian_pin=self.custodian_pin,
            witness_receipt=self.witness_receipt,
            trusted_witness_key=self.witness.public_key(),
            expected_witness_pin=self.witness_pin,
            observer_public_key=self.observer.public_key(),
            worker_fingerprint=self.worker_pin,
            observer_id="observer-test", worker_id="worker-test",
            expected_witness_head=self.witness_head,
            expected_observer_head=self.obs_head,
            expected_scope=SCOPE, expected_generation=1,
            expected_previous_checkpoint_sha256=ZERO,
            expected_checkpoint_sha256=checkpoint_digest(self.checkpoint),
            trusted_now=1700000050, minimum_witness_count=0)

    _UNSET = object()

    def verify(self, envelope=_UNSET, **kwargs):
        return verify_research_checkpoint(
            self.checkpoint if envelope is self._UNSET else envelope,
            **{**self.verify_args, **kwargs})

    def test_exact_detached_signature_requires_four_role_pins(self):
        self.assertTrue(self.verify())
        payload = self.checkpoint["payload"]
        self.assertEqual(len({
            payload[k] for k in (
                "custodian_fingerprint", "witness_fingerprint",
                "observer_fingerprint", "worker_fingerprint")}), 4)
        self.assertFalse(payload["independent_custody_verified"])
        self.assertFalse(payload["source_transaction_attested"])
        self.assertFalse(payload["agent_dispatch_authorized"])
        self.assertEqual(payload["decision"], "HOLD")

    def test_missing_trusted_checkpoint_digest_denied(self):
        for missing in (None, "", ZERO, "f"*64):
            with self.subTest(hash=missing):
                self.assertFalse(self.verify(expected_checkpoint_sha256=missing))

    def test_missing_or_forged_external_custodian_pin_denied(self):
        for wrong in (None, "", "f"*64, self.witness_pin):
            with self.subTest(pin=wrong):
                self.assertFalse(self.verify(expected_custodian_pin=wrong))

    def test_custodian_key_substitution_denied(self):
        self.assertFalse(self.verify(
            trusted_custodian_key=Ed25519PrivateKey.generate().public_key()))

    def test_key_role_overlap_rejected_before_sign(self):
        with self.assertRaises(UnsafeCustodyCheckpoint):
            sign_research_checkpoint(
                **{**self.sign_args, "custodian_signer": self.witness})

    def test_witness_signature_wrong_key_denied_before_sign(self):
        with self.assertRaises(UnsafeCustodyCheckpoint):
            sign_research_checkpoint(
                **{**self.sign_args,
                   "trusted_witness_key":
                   Ed25519PrivateKey.generate().public_key()})

    def test_changed_witness_receipt_denied(self):
        bad = deepcopy(self.witness_receipt)
        bad["payload"]["observer_revision"] = 3
        self.assertFalse(self.verify(witness_receipt=bad))
        with self.assertRaises(UnsafeCustodyCheckpoint):
            sign_research_checkpoint(
                **{**self.sign_args, "witness_receipt": bad})

    def test_stale_signed_receipt_denied_against_new_head(self):
        self.assertFalse(self.verify(expected_witness_head=(2, "f"*64)))
        self.assertFalse(self.verify(expected_observer_head=(1, "f"*64)))
        self.assertFalse(self.verify(minimum_witness_count=1))

    def test_wrong_pinned_worker_identity_denied(self):
        self.assertFalse(self.verify(worker_fingerprint="f"*64))

    def test_wrong_observer_key_denied(self):
        self.assertFalse(self.verify(
            observer_public_key=Ed25519PrivateKey.generate().public_key()))

    def test_wrong_witness_pin_denied(self):
        self.assertFalse(self.verify(expected_witness_pin="f"*64))

    def test_custody_scope_mismatch_denied(self):
        self.assertFalse(self.verify(expected_scope="another.scope"))

    def test_wrong_generation_denied(self):
        self.assertFalse(self.verify(expected_generation=2))
        self.assertFalse(self.verify(expected_generation=True))

    def test_wrong_previous_checkpoint_hash_denied(self):
        self.assertFalse(self.verify(
            expected_previous_checkpoint_sha256="f"*64))
        self.assertFalse(self.verify(
            expected_previous_checkpoint_sha256=None))

    def test_expired_and_future_dated_denied(self):
        self.assertFalse(self.verify(trusted_now=1700000600))
        self.assertFalse(self.verify(trusted_now=1699999999))

    def test_non_integer_clock_denied(self):
        for clock in (True, "1700000050", 1700000050.0, None):
            self.assertFalse(self.verify(trusted_now=clock))

    def test_signed_policy_fails_after_payload_tamper(self):
        bad = deepcopy(self.checkpoint)
        bad["payload"]["scope"] = "fraudulent.scope"
        self.assertFalse(self.verify(envelope=bad))

    def test_self_declared_external_independence_rejected(self):
        bad = deepcopy(self.checkpoint)
        bad["payload"]["independent_custody_verified"] = True
        self.assertFalse(self.verify(envelope=bad))

    def test_self_declared_dispatch_rejected(self):
        bad = deepcopy(self.checkpoint)
        bad["payload"]["agent_dispatch_authorized"] = True
        self.assertFalse(self.verify(envelope=bad))

    def test_extra_unrecognized_metadata_rejected(self):
        bad = deepcopy(self.checkpoint)
        bad["payload"]["admin_approved"] = True
        self.assertFalse(self.verify(envelope=bad))

    def test_changed_signature_rejected(self):
        bad = deepcopy(self.checkpoint)
        bad["signature"] = "AAAA" + bad["signature"][4:]
        self.assertFalse(self.verify(envelope=bad))

    def test_invalid_receipt_input_type_denied(self):
        for value in ({}, [], "bad", None):
            with self.subTest(item=str(type(value))):
                self.assertFalse(self.verify(envelope=value))

    def test_missing_previous_or_reused_genesis_anchor_denied_on_next_generation(self):
        for previous in (ZERO, "", None):
            with self.subTest(prev=previous):
                with self.assertRaises(UnsafeCustodyCheckpoint):
                    sign_research_checkpoint(
                        **{**self.sign_args,
                           "generation": 2,
                           "previous_checkpoint_sha256": previous})
        stage2 = sign_research_checkpoint(
            **{**self.sign_args,
               "generation": 2,
               "previous_checkpoint_sha256":
               checkpoint_digest(self.checkpoint)})
        self.assertTrue(verify_research_checkpoint(
            stage2, **{**self.verify_args,
                       "expected_generation": 2,
                       "expected_previous_checkpoint_sha256":
                       checkpoint_digest(self.checkpoint),
                       "expected_checkpoint_sha256":
                       checkpoint_digest(stage2)}))
        # A second endorsement of same witness head fails when a newer
        # independently held minimum record count is provided.
        self.assertFalse(verify_research_checkpoint(
            stage2, **{**self.verify_args,
                       "expected_generation": 2,
                       "expected_previous_checkpoint_sha256":
                       checkpoint_digest(self.checkpoint),
                       "expected_checkpoint_sha256":
                       checkpoint_digest(stage2),
                       "minimum_witness_count": 1}))

    def test_oversized_validity_period_refused(self):
        with self.assertRaises(UnsafeCustodyCheckpoint):
            sign_research_checkpoint(
                **{**self.sign_args, "expires_at": 1700007200})

    def test_wrong_roles_and_wrong_pins_never_fall_back_to_payload(self):
        self.assertFalse(self.verify(observer_id="not-expected"))
        self.assertFalse(self.verify(worker_id="not-expected"))
        self.assertFalse(self.verify(expected_checkpoint_sha256=None))

    def test_rollback_of_checkpoint_and_external_floor_remains_attack(self):
        # Synthetic signature is truthful about the OLD image. If the
        # verifier receives a rolled-back expected digest/sequence/floor
        # from the same admin, it cannot prove a newer state existed.
        self.assertTrue(self.verify(minimum_witness_count=0))
        self.assertFalse(self.verify(minimum_witness_count=1))


if __name__ == "__main__":
    unittest.main()
