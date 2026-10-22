"""Synthetic off-host witness signs its own exact-scoped readback receipt."""
from __future__ import annotations

import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from observer_checkpoint import ZERO, public_fingerprint
from v2_durable_observer_inbox import SyntheticV2DurableObserverInbox
from v2_offhost_marker_witness import V2OffhostMarkerWitness, UnsafeOffhostWitness
from v2_witness_countersign import (
    UnsafeCountersign, _private_fixture_key, countersign,
    verify_witness_receipt,
)

BEFORE, AFTER = "a" * 64, "b" * 64
OP = "1" * 32


class CountersignTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="og-v2-countersign-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.root.chmod(0o700)
        self.observer = Ed25519PrivateKey.generate()
        self.worker = Ed25519PrivateKey.generate()
        self.witness_key_path = self.root / "witness-ephemeral.key"
        self.signer = _private_fixture_key(self.witness_key_path, create=True)
        self.signer_public = self.signer.public_key()
        self.witness_pin = public_fingerprint(self.signer_public)
        self.worker_pin = public_fingerprint(self.worker.public_key())
        self.observer_pin = public_fingerprint(self.observer.public_key())
        self.inbox_path = self.root / "observer.sqlite3"
        self.inbox = SyntheticV2DurableObserverInbox(
            self.inbox_path, observer_signer=self.observer,
            worker_public_key=self.worker.public_key(),
            observer_id="observer", worker_id="worker", create=True,
            initial_state=BEFORE)
        self.genesis = self.inbox.export_signed_marker(
            trusted_anchor=self.inbox.created_head)
        self.log_path = self.root / "witness.jsonl"
        self.witness = V2OffhostMarkerWitness(
            self.log_path, observer_public_key=self.observer.public_key(),
            observer_id="observer", worker_id="worker",
            worker_pin=self.worker_pin)
        self.head0 = self.witness.append(
            self.genesis, trusted_anchor=None, genesis=True)
        self.marker_head0 = self.inbox.created_head
        self.receipt0 = countersign(
            self.witness, signer=self.signer,
            witness_head=self.head0, observer_head=self.marker_head0)
        self.expected = dict(
            trusted_witness_key=self.signer_public,
            expected_witness_pin=self.witness_pin,
            observer_public_key=self.observer.public_key(),
            worker_fingerprint=self.worker_pin,
            observer_id="observer", worker_id="worker",
            expected_witness_head=self.head0,
            expected_observer_head=self.marker_head0,
        )

    def test_countersigned_genesis_exact_public_pins(self):
        self.assertTrue(verify_witness_receipt(self.receipt0, **self.expected))
        p = self.receipt0["payload"]
        self.assertEqual(p["observer_stage"], "empty")
        self.assertFalse(p["agent_dispatch_authorized"])
        self.assertEqual(p["provider_requests_forwarded"], 0)
        self.assertNotIn("private", str(p).lower())

    def test_ephemeral_signer_persists_across_cooperative_restarts(self):
        fresh = _private_fixture_key(self.witness_key_path)
        signed = countersign(
            self.witness, signer=fresh,
            witness_head=self.head0, observer_head=self.marker_head0)
        self.assertEqual(self.receipt0, signed)
        self.assertEqual(self.witness_key_path.stat().st_mode & 0o777, 0o600)

    def test_second_bootstrap_is_denied(self):
        with self.assertRaises(FileExistsError):
            _private_fixture_key(self.witness_key_path, create=True)

    def test_wrong_witness_signer_not_accepted_by_pinned_public_key(self):
        rogue = Ed25519PrivateKey.generate()
        forged = countersign(
            self.witness, signer=rogue,
            witness_head=self.head0, observer_head=self.marker_head0)
        self.assertFalse(verify_witness_receipt(forged, **self.expected))

    def test_wrong_outside_key_pin_denied(self):
        self.assertFalse(verify_witness_receipt(
            self.receipt0, **{**self.expected,
                             "expected_witness_pin": "f" * 64}))

    def test_wrong_expected_worker_scope_denied(self):
        self.assertFalse(verify_witness_receipt(
            self.receipt0, **{**self.expected,
                             "worker_fingerprint": "f" * 64}))

    def test_wrong_observer_scope_denied(self):
        self.assertFalse(verify_witness_receipt(
            self.receipt0, **{**self.expected, "observer_id": "other"}))
        self.assertFalse(verify_witness_receipt(
            self.receipt0, **{**self.expected, "worker_id": "other"}))

    def test_missing_or_stale_remote_anchor_denied(self):
        with self.assertRaises(UnsafeOffhostWitness):
            countersign(
                self.witness, signer=self.signer,
                witness_head=(2, "f"*64), observer_head=self.marker_head0)
        with self.assertRaises(UnsafeOffhostWitness):
            countersign(
                self.witness, signer=self.signer,
                witness_head=self.head0, observer_head=(1, "f"*64))

    def test_wrong_expected_receipt_head_denied(self):
        self.assertFalse(verify_witness_receipt(
            self.receipt0, **{**self.expected,
                             "expected_witness_head": (2, self.head0[1])}))
        self.assertFalse(verify_witness_receipt(
            self.receipt0, **{**self.expected,
                             "expected_observer_head": (1, self.marker_head0[1])}))

    def test_tampered_witness_signed_receipt_denied(self):
        forged = deepcopy(self.receipt0)
        forged["payload"]["observer_stage"] = "delivered"
        self.assertFalse(verify_witness_receipt(forged, **self.expected))

    def test_forged_dispatch_field_denied(self):
        forged = deepcopy(self.receipt0)
        forged["payload"]["agent_dispatch_authorized"] = True
        self.assertFalse(verify_witness_receipt(forged, **self.expected))

    def test_witness_signature_wrong_domain_or_mutated(self):
        forged = deepcopy(self.receipt0)
        forged["signature"] = "AAAA" + forged["signature"][4:]
        self.assertFalse(verify_witness_receipt(forged, **self.expected))

    def test_signer_key_symlink_denied(self):
        old = self.root / "original.key"
        self.witness_key_path.rename(old)
        self.witness_key_path.symlink_to(old)
        with self.assertRaises(OSError):
            _private_fixture_key(self.witness_key_path)

    def test_signer_key_hardlink_denied(self):
        os.link(self.witness_key_path, self.root / "hardlink.key")
        with self.assertRaises(UnsafeCountersign):
            _private_fixture_key(self.witness_key_path)

    def test_signer_key_world_readable_denied(self):
        self.witness_key_path.chmod(0o644)
        with self.assertRaises(UnsafeCountersign):
            _private_fixture_key(self.witness_key_path)

    def test_wrong_role_key_directory_permissions_denied(self):
        self.root.chmod(0o755)
        with self.assertRaises(UnsafeCountersign):
            _private_fixture_key(self.witness_key_path)

    def test_empty_or_wrong_type_receipt_denied(self):
        for doc in ({}, [], None, self.receipt0["payload"]):
            with self.subTest(doc=str(type(doc))):
                self.assertFalse(verify_witness_receipt(doc, **self.expected))

    def test_research_old_db_with_old_anchor_remains_accepted(self):
        # Countersigning an old LOG WITH a stale head is possible if
        # BOTH can be rolled back by a shared same-UID administrator.
        old = self.root / "old.jsonl"
        old.write_bytes(self.log_path.read_bytes())
        self.assertTrue(verify_witness_receipt(
            countersign(self.witness, signer=self.signer,
                        witness_head=self.head0, observer_head=self.marker_head0),
            **self.expected))
        self.assertFalse(self.receipt0["payload"]["agent_dispatch_authorized"])


if __name__ == "__main__":
    unittest.main()
