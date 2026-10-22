"""Restart/replay/rollback tests for deny-only synthetic worker receipt journal."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from durable_worker_receipts import (
    SyntheticWorkerReceiptJournal, UnsafeWorkerReceipt,
)
from observer_checkpoint import ZERO, public_fingerprint

def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

BEFORE, AFTER, THIRD = (
    digest(b"fixture_before"), digest(b"fixture_after"), digest(b"fixture_third"),
)
OP1, OP2 = ("a" * 32, "b" * 32)
REQUEST1, REQUEST2 = digest(b"r1"), digest(b"r2")
PREP1, PREP2 = digest(b"prepare1"), digest(b"prepare2")
ACK1, ACK2 = digest(b"ack1"), digest(b"ack2")
CHECKPOINT1, CHECKPOINT2 = digest(b"checkpoint1"), digest(b"checkpoint2")


class WorkerJournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="og-worker-receipt-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        os.chmod(self.root, 0o700)
        self.db = self.root / "worker-receipts.sqlite3"
        self.key = Ed25519PrivateKey.generate()
        self.observer = Ed25519PrivateKey.generate()
        self.observer_pin = public_fingerprint(self.observer.public_key())
        self.journal = self.open(create=True)
        self.empty = (0, ZERO)

    def open(self, *, signer="original", key=None, create=False):
        private = self.key if signer == "original" else (
            None if signer is None else signer)
        public = (key or self.key.public_key())
        return SyntheticWorkerReceiptJournal(
            self.db, worker_public_key=public, worker_id="lenovo-fixture",
            observer_id="x1-observer", observer_fingerprint=self.observer_pin,
            signer=private, create=create,
            initial_state=BEFORE if create else None)

    def commit(self, *, op=OP1, before=BEFORE, after=AFTER,
               generation=2, req=REQUEST1, prep=PREP1):
        return {
            "worker_id": "lenovo-fixture", "observer_id": "x1-observer",
            "operation_id": op, "request_sha256": req,
            "prepare_sha256": prep, "before_state_sha256": before,
            "after_state_sha256": after, "generation": generation,
            "sequence": 1, "receipt_number": 1,
            "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0,
        }

    def deliver(self, commit, *, ack=ACK1, checkpoint=CHECKPOINT1):
        return {
            **commit, "observer_ack_sha256": ack,
            "observer_checkpoint_sha256": checkpoint,
        }

    def first(self):
        fields = self.commit()
        head, signed = self.journal.append(
            kind="synthetic_worker_commit", fields=fields,
            trusted_anchor=self.empty)
        return fields, head, signed

    def complete(self):
        fields, head, signed = self.first()
        nexthead, delivered = self.open().append(
            kind="synthetic_worker_delivery",
            fields=self.deliver(fields), trusted_anchor=head)
        return fields, head, nexthead, signed, delivered

    def test_private_db_and_empty_head(self):
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        state = self.journal.inspect(
            trusted_anchor=self.empty, observed_worker_state_sha256=BEFORE)
        self.assertTrue(state["worker_state_matches_supplied_digest"])
        self.assertEqual(state["record_count"], 0)
        self.assertEqual(state["decision"], "HOLD")
        self.assertFalse(state["agent_dispatch_authorized"])

    def test_commit_survives_restarted_reader_and_stays_fenced(self):
        fields, head, _ = self.first()
        new = self.open()
        state = new.inspect(
            trusted_anchor=head, observed_worker_state_sha256=AFTER)
        self.assertTrue(state["commit_pending_delivery"])
        self.assertTrue(state["worker_state_matches_supplied_digest"])
        with self.assertRaises(UnsafeWorkerReceipt):
            new.append(kind="synthetic_worker_commit",
                       fields=self.commit(op=OP2, before=AFTER, after=THIRD,
                                          generation=3),
                       trusted_anchor=head)

    def test_delivery_binds_commit_and_clears_local_pending(self):
        _, _, complete, signed, delivered = self.complete()
        outcome = self.open().inspect(
            trusted_anchor=complete, observed_worker_state_sha256=AFTER)
        self.assertEqual(outcome["record_count"], 2)
        self.assertFalse(outcome["commit_pending_delivery"])
        self.assertEqual(delivered["payload"]["observer_ack_sha256"], ACK1)
        self.assertFalse(delivered["payload"]["agent_dispatch_authorized"])

    def test_next_commit_must_chain_state_and_generation(self):
        _, _, head, _, _ = self.complete()
        second = self.commit(
            op=OP2, before=AFTER, after=THIRD, generation=3,
            req=REQUEST2, prep=PREP2)
        updated, _ = self.open().append(
            kind="synthetic_worker_commit", fields=second,
            trusted_anchor=head)
        _, _ = self.open().append(
            kind="synthetic_worker_delivery",
            fields=self.deliver(second, ack=ACK2,
                                checkpoint=CHECKPOINT2),
            trusted_anchor=updated)

    def test_duplicate_commit_op_rejected(self):
        _, head, _ = self.first()
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open().append(kind="synthetic_worker_commit",
                               fields=self.commit(), trusted_anchor=head)

    def test_delivery_without_commit_rejected(self):
        with self.assertRaises(UnsafeWorkerReceipt):
            self.journal.append(kind="synthetic_worker_delivery",
                                fields=self.deliver(self.commit()),
                                trusted_anchor=self.empty)

    def test_delivery_wrong_ack_is_a_new_signed_claim_not_independently_verified(self):
        # Important honesty test: the local journal validates receipt
        # consistency and signing key, not external observer ACK authority.
        fields, head, _ = self.first()
        bogus = self.deliver(fields, ack="f" * 64)
        newhead, _ = self.open().append(
            kind="synthetic_worker_delivery",
            fields=bogus, trusted_anchor=head)
        self.assertFalse(self.open().inspect(
            trusted_anchor=newhead)["source_authority_verified"])

    def test_delivery_wrong_scope_denied(self):
        fields, head, _ = self.first()
        for k, value in {
            "operation_id": OP2,
            "after_state_sha256": THIRD,
            "sequence": 2,
            "generation": 3,
            "request_sha256": REQUEST2,
        }.items():
            with self.subTest(field=k), self.assertRaises(UnsafeWorkerReceipt):
                self.open().append(
                    kind="synthetic_worker_delivery",
                    fields={**self.deliver(fields), k: value},
                    trusted_anchor=head)

    def test_externally_pinned_head_required_to_inspect(self):
        _, head, _ = self.first()
        for anchor in ((0, ZERO), (1, "f" * 64), None, [1, head[1]]):
            with self.subTest(anchor=anchor), self.assertRaises(UnsafeWorkerReceipt):
                self.open().inspect(trusted_anchor=anchor)

    def test_stale_writer_denied(self):
        fields, head, _ = self.first()
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open().append(kind="synthetic_worker_delivery",
                               fields=self.deliver(fields),
                               trusted_anchor=self.empty)

    def test_valid_old_db_rejected_against_new_head(self):
        backup = self.root / "valid-old-snapshot"
        shutil.copy2(self.db, backup)
        _, head, _ = self.first()
        shutil.copy2(backup, self.db)
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open().inspect(trusted_anchor=head)

    def test_old_db_and_old_head_still_passes_negative_control(self):
        backup = self.root / "valid-old-snapshot"
        shutil.copy2(self.db, backup)
        self.first()
        shutil.copy2(backup, self.db)
        state = self.open().inspect(trusted_anchor=self.empty)
        self.assertEqual(state["record_count"], 0)
        self.assertFalse(state["rollback_resistance_verified"])

    def test_wrong_signer_key_or_pin_denied(self):
        self.first()
        other = Ed25519PrivateKey.generate()
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open(key=other.public_key())
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open(signer=other)

    def test_reused_observer_key_denied(self):
        with self.assertRaises(UnsafeWorkerReceipt):
            SyntheticWorkerReceiptJournal(
                self.db, worker_public_key=self.observer.public_key(),
                worker_id="lenovo-fixture", observer_id="x1-observer",
                observer_fingerprint=self.observer_pin,
                signer=self.observer)

    def test_read_only_verifier_cannot_append(self):
        reader = self.open(signer=None)
        with self.assertRaises(UnsafeWorkerReceipt):
            reader.append(kind="synthetic_worker_commit",
                          fields=self.commit(), trusted_anchor=self.empty)

    def test_missing_db_fails_closed(self):
        self.db.unlink()
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open()
        self.assertFalse(self.db.exists())

    def test_existing_db_cannot_be_recreated(self):
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open(create=True)

    def test_truncated_db_fails_closed(self):
        self.db.write_bytes(b"not SQLite")
        with self.assertRaises((UnsafeWorkerReceipt, sqlite3.Error)):
            self.open()

    def test_hardlink_and_symlink_fails_closed(self):
        os.link(self.db, self.root / "linked")
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open()
        (self.root / "linked").unlink()
        self.db.rename(self.root / "original")
        self.db.symlink_to(self.root / "original")
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open()

    def test_exposed_permissions_fails_closed(self):
        os.chmod(self.db, 0o644)
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open()

    def test_exposed_directory_fails_closed(self):
        os.chmod(self.root, 0o755)
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open()

    def test_deleted_signed_record_fails_even_with_updated_external_head(self):
        self.complete()
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM receipts WHERE sequence=1")
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open()

    def test_signed_record_mutation_denied(self):
        self.first()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE receipts SET signed_receipt='{}' WHERE sequence=1")
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open()

    def test_hash_mutation_denied(self):
        self.first()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE receipts SET head_sha256=? WHERE sequence=1", ("f"*64,))
        with self.assertRaises(UnsafeWorkerReceipt):
            self.open()

    def test_concurrent_same_head_one_writer_wins(self):
        fields = self.commit()
        barrier = threading.Barrier(8)
        statuses = []
        guard = threading.Lock()
        def attempt():
            try:
                barrier.wait(timeout=5)
                self.open().append(
                    kind="synthetic_worker_commit", fields=fields,
                    trusted_anchor=self.empty)
                answer = "accepted"
            except (UnsafeWorkerReceipt, sqlite3.Error):
                answer = "denied"
            with guard:
                statuses.append(answer)
        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for th in threads: th.start()
        for th in threads: th.join(timeout=10)
        self.assertEqual(len(statuses), 8)
        self.assertEqual(statuses.count("accepted"), 1)
        self.assertEqual(statuses.count("denied"), 7)


if __name__ == "__main__":
    unittest.main()
