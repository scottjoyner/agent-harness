"""All signatures here are synthetic and never grant model or agent access."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from durable_lease_store import UnsafeLeaseState
from observer_checkpoint import ZERO, public_fingerprint
from v2_outbox_publication import (
    worker_publish, observer_verify_publication, observer_sign_ack,
    worker_verify_ack, worker_sign_delivery, observer_verify_delivery,
    publication_sha256,
)

SECRET = hashlib.sha256(b"isolated signed-v2-worker-fixture").digest()
WORKTREE = hashlib.sha256(b"synthetic-v2-test").hexdigest()
OPERATION = "a" * 32


class V2PublicationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="og-v2-publication-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        os.chmod(self.root, 0o700)
        self.db = self.root / "v2.sqlite3"
        self.gate = AtomicSyntheticLeaseOutbox(
            self.db, SECRET, create=True, now=lambda: 1000)
        self.worker = Ed25519PrivateKey.generate()
        self.observer = Ed25519PrivateKey.generate()
        self.worker_pin = public_fingerprint(self.worker.public_key())
        self.observer_pin = public_fingerprint(self.observer.public_key())
        self.token = self.gate.mint(
            uid=1000, owner="synthetic-owner",
            session="synthetic-session", worktree_sha256=WORKTREE,
            model_pin="kilo/kilo-auto/free")
        self.request = {
            "schema": 1, "op": "synthetic_receipt",
            "token": self.token, "seq": 1,
        }
        self.before = self.gate.state_digest()
        self.result = self.gate.check(
            self.request, operation_id=OPERATION, peer_uid=1000,
            expected_owner="synthetic-owner",
            expected_session="synthetic-session",
            expected_worktree_sha256=WORKTREE,
            expected_model_pin="kilo/kilo-auto/free")
        self.after = self.gate.state_digest()
        self.head = (1, self.result["atomic_outbox_sha256"])
        self.exported = self.gate.export_head_event(trusted_anchor=self.head)
        self.publication = worker_publish(
            signer=self.worker,
            worker_id="lenovo-fixture",
            observer_id="x1-fixture",
            observer_fingerprint=self.observer_pin,
            exported_head=self.exported)
        self.verifier = dict(
            worker_public_key=self.worker.public_key(),
            trusted_worker_fingerprint=self.worker_pin,
            trusted_observer_fingerprint=self.observer_pin,
            expected_worker_id="lenovo-fixture",
            expected_observer_id="x1-fixture",
            expected_operation_id=OPERATION,
            expected_outbox_head=self.head,
            expected_before_state=self.before,
            expected_after_state=self.after)
        self.ack_args = dict(
            signer=self.observer, publication=self.publication,
            worker_public_key=self.worker.public_key(),
            trusted_worker_fingerprint=self.worker_pin,
            expected_worker_id="lenovo-fixture",
            expected_observer_id="x1-fixture",
            expected_operation_id=OPERATION,
            expected_outbox_head=self.head,
            expected_before_state=self.before,
            expected_after_state=self.after,
            observer_generation=2,
            previous_checkpoint_sha256=ZERO)
        self.ack = observer_sign_ack(**self.ack_args)

    def test_exact_head_export_protects_operation(self):
        self.assertEqual(self.exported["outbox_event"]["operation_id"], OPERATION)
        self.assertEqual(self.exported["lease_state_sha256"], self.after)
        self.assertNotIn(self.token, str(self.exported))
        self.assertNotIn("hmac_sha256", str(self.exported))
        self.assertFalse(self.exported["agent_dispatch_authorized"])

    def test_missing_stale_and_wrong_head_fail_closed(self):
        for anchor in (None, (0, ZERO), (1, "f"*64), [1, self.head[1]]):
            with self.subTest(anchor=anchor):
                with self.assertRaises(UnsafeLeaseState):
                    self.gate.export_head_event(trusted_anchor=anchor)

    def test_valid_worker_publication_and_observer_ack(self):
        self.assertTrue(observer_verify_publication(
            envelope=self.publication, **self.verifier))
        self.assertTrue(worker_verify_ack(
            envelope=self.ack,
            observer_public_key=self.observer.public_key(),
            trusted_observer_fingerprint=self.observer_pin,
            publication=self.publication, expected_generation=2,
            previous_checkpoint_sha256=ZERO))
        self.assertFalse(self.publication["payload"]["agent_dispatch_authorized"])
        self.assertEqual(self.publication["payload"]["provider_requests_forwarded"], 0)

    def test_wrong_worker_pin_rejected(self):
        self.assertFalse(observer_verify_publication(
            envelope=self.publication,
            **{**self.verifier, "trusted_worker_fingerprint": "f"*64}))

    def test_different_worker_key_rejected(self):
        rogue = Ed25519PrivateKey.generate()
        self.assertFalse(observer_verify_publication(
            envelope=self.publication,
            **{**self.verifier, "worker_public_key": rogue.public_key()}))

    def test_wrong_observer_pin_rejected(self):
        self.assertFalse(observer_verify_publication(
            envelope=self.publication,
            **{**self.verifier, "trusted_observer_fingerprint": "e"*64}))

    def test_wrong_operation_id_rejected(self):
        self.assertFalse(observer_verify_publication(
            envelope=self.publication,
            **{**self.verifier, "expected_operation_id": "b"*32}))

    def test_wrong_previous_or_resulting_state_rejected(self):
        for key in ("expected_before_state", "expected_after_state"):
            with self.subTest(key=key):
                self.assertFalse(observer_verify_publication(
                    envelope=self.publication,
                    **{**self.verifier, key: "c"*64}))

    def test_stale_head_and_sequence_rejected(self):
        self.assertFalse(observer_verify_publication(
            envelope=self.publication,
            **{**self.verifier, "expected_outbox_head": (2, self.head[1])}))

    def test_modified_signature_payload_rejected(self):
        bad = deepcopy(self.publication)
        bad["payload"]["outbox_event"]["receipt_number"] = 2
        self.assertFalse(observer_verify_publication(
            envelope=bad, **self.verifier))

    def test_modified_head_hash_rejected(self):
        bad = deepcopy(self.publication)
        bad["payload"]["outbox_record_sha256"] = "f"*64
        self.assertFalse(observer_verify_publication(
            envelope=bad, **self.verifier))

    def test_extra_or_removed_claim_rejected(self):
        for record in (
            {**self.publication, "something": True},
            {**self.publication, "signature": "invalid"},
        ):
            self.assertFalse(observer_verify_publication(
                envelope=record, **self.verifier))

    def test_observer_signer_refuses_unverified_publication(self):
        bad = deepcopy(self.publication)
        bad["payload"]["outbox_event"]["operation_id"] = "b"*32
        with self.assertRaises(ValueError):
            observer_sign_ack(**{**self.ack_args, "publication": bad})

    def test_observer_refuses_wrong_expected_worker_key(self):
        rogue = Ed25519PrivateKey.generate()
        with self.assertRaises(ValueError):
            observer_sign_ack(**{
                **self.ack_args,
                "worker_public_key": rogue.public_key(),
            })

    def test_observer_signer_refuses_unexpected_head(self):
        with self.assertRaises(ValueError):
            observer_sign_ack(**{
                **self.ack_args, "expected_outbox_head": (0, ZERO)})

    def test_forged_ack_payload_rejected(self):
        bad = deepcopy(self.ack)
        bad["payload"]["outbox_record_sha256"] = "f"*64
        self.assertFalse(worker_verify_ack(
            envelope=bad, observer_public_key=self.observer.public_key(),
            trusted_observer_fingerprint=self.observer_pin,
            publication=self.publication, expected_generation=2,
            previous_checkpoint_sha256=ZERO))

    def delivery(self, ack=None):
        return worker_sign_delivery(
            signer=self.worker, publication=self.publication,
            observer_ack=self.ack if ack is None else ack,
            observer_public_key=self.observer.public_key(),
            trusted_observer_fingerprint=self.observer_pin,
            expected_generation=2, previous_checkpoint_sha256=ZERO)

    def test_worker_delivery_receipt_after_valid_ack_only(self):
        signed = self.delivery()
        self.assertTrue(observer_verify_delivery(
            envelope=signed,
            worker_public_key=self.worker.public_key(),
            trusted_worker_fingerprint=self.worker_pin,
            trusted_observer_fingerprint=self.observer_pin,
            publication=self.publication, observer_ack=self.ack))
        self.assertFalse(signed["payload"]["agent_dispatch_authorized"])

    def test_invalid_ack_cannot_trigger_worker_delivery(self):
        bad = deepcopy(self.ack)
        bad["payload"]["operation_id"] = "b"*32
        with self.assertRaises(ValueError):
            self.delivery(bad)

    def test_replayed_delivery_for_different_ack_is_rejected(self):
        signed = self.delivery()
        other = deepcopy(self.ack)
        other["signature"] = other["signature"][::-1]
        self.assertFalse(observer_verify_delivery(
            envelope=signed, worker_public_key=self.worker.public_key(),
            trusted_worker_fingerprint=self.worker_pin,
            trusted_observer_fingerprint=self.observer_pin,
            publication=self.publication, observer_ack=other))

    def test_tampered_delivery_receipt_rejected(self):
        signed = self.delivery()
        forged = deepcopy(signed)
        forged["payload"]["outbox_record_sha256"] = "f"*64
        self.assertFalse(observer_verify_delivery(
            envelope=forged, worker_public_key=self.worker.public_key(),
            trusted_worker_fingerprint=self.worker_pin,
            trusted_observer_fingerprint=self.observer_pin,
            publication=self.publication, observer_ack=self.ack))

    def test_delivery_signer_must_equal_publication_key(self):
        rogue = Ed25519PrivateKey.generate()
        with self.assertRaises(ValueError):
            worker_sign_delivery(
                signer=rogue, publication=self.publication,
                observer_ack=self.ack,
                observer_public_key=self.observer.public_key(),
                trusted_observer_fingerprint=self.observer_pin,
                expected_generation=2,
                previous_checkpoint_sha256=ZERO)

    def test_wrong_observer_public_key_rejected(self):
        other = Ed25519PrivateKey.generate()
        self.assertFalse(worker_verify_ack(
            envelope=self.ack, observer_public_key=other.public_key(),
            trusted_observer_fingerprint=self.observer_pin,
            publication=self.publication, expected_generation=2,
            previous_checkpoint_sha256=ZERO))

    def test_wrong_observer_generation_or_previous_checkpoint_rejected(self):
        for generation, previous in ((3, ZERO), (2, "f"*64)):
            with self.subTest(generation=generation, previous=previous):
                self.assertFalse(worker_verify_ack(
                    envelope=self.ack, observer_public_key=self.observer.public_key(),
                    trusted_observer_fingerprint=self.observer_pin,
                    publication=self.publication, expected_generation=generation,
                    previous_checkpoint_sha256=previous))

    def test_replay_observer_ack_with_different_publication_rejected(self):
        forged = deepcopy(self.publication)
        # Guarantee a genuinely different, still-base64url signature.
        # Using a fixed "X" was flaky when the random Ed25519
        # signature already started with X (~1/64 of runs).
        original_signature = forged["signature"]
        forged["signature"] = (
            ("A" if original_signature[0] != "A" else "B")
            + original_signature[1:])
        self.assertNotEqual(original_signature, forged["signature"])
        self.assertFalse(worker_verify_ack(
            envelope=self.ack, observer_public_key=self.observer.public_key(),
            trusted_observer_fingerprint=self.observer_pin,
            publication=forged, expected_generation=2,
            previous_checkpoint_sha256=ZERO))  # iff pub hash mismatch

    def test_old_valid_v2_image_and_newer_head_rejected(self):
        old = self.root / "old_valid_image"
        shutil.copy2(self.db, old)
        request2 = {**self.request, "seq": 2}
        second = self.gate.check(
            request2, operation_id="b"*32, peer_uid=1000,
            expected_owner="synthetic-owner",
            expected_session="synthetic-session",
            expected_worktree_sha256=WORKTREE,
            expected_model_pin="kilo/kilo-auto/free")
        current_head = (2, second["atomic_outbox_sha256"])
        shutil.copy2(old, self.db)
        with self.assertRaises(UnsafeLeaseState):
            self.gate.export_head_event(trusted_anchor=current_head)
        self.assertEqual(self.gate.export_head_event(
            trusted_anchor=self.head)["outbox_event"]["record_no"], 1)


if __name__ == "__main__":
    unittest.main()
