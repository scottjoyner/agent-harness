"""Synthetic observer ACK journal tests: no production authorizations."""
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from durable_observer_journal import ResearchObserverJournal, ObserverJournalError
from observer_ack_protocol import verify_ack
from observer_checkpoint import checkpoint_sha256, public_fingerprint

BEFORE = hashlib.sha256(b"synthetic worker state A").hexdigest()
AFTER = hashlib.sha256(b"synthetic worker state B").hexdigest()
AFTER2 = hashlib.sha256(b"synthetic worker state C").hexdigest()
REQUEST = hashlib.sha256(b"synthetic request").hexdigest()
OP = "a" * 32
OP2 = "b" * 32


class DurableJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="og-journal-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.path = self.root / "observer.sqlite3"
        self.key = Ed25519PrivateKey.generate()
        self.journal = self.open(create=True)

    def open(self, *, create=False, key=None, **args):
        return ResearchObserverJournal(
            self.path, signer=key or self.key,
            observer_id="observer-fixture",
            worker_id="worker-fixture",
            initial_state=BEFORE if create else None,
            create=create, **args)

    def prepare(self, op=OP, before=BEFORE):
        return self.journal.prepare(
            operation_id=op, request_sha256=REQUEST,
            before_state_sha256=before)

    def ack(self, prepared, after=AFTER):
        return self.journal.acknowledge(
            prepared=prepared, after_state_sha256=after,
            externally_observed_sha256=after)

    def test_private_file_mode(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.journal.state()["agent_dispatch_authorized"])

    def test_prepare_fence_survives_process_restart(self):
        prepared = self.prepare()
        reopened = self.open()
        self.assertTrue(reopened.state()["unresolved"])
        with self.assertRaises(ObserverJournalError):
            reopened.prepare(
                operation_id=OP2, request_sha256=REQUEST,
                before_state_sha256=BEFORE)
        reopened.abort_prepared(
            prepared=prepared, observed_worker_sha256=BEFORE)
        self.assertFalse(self.open().state()["unresolved"])
        self.open().prepare(operation_id=OP2, request_sha256=REQUEST,
                            before_state_sha256=BEFORE)

    def test_prepared_cannot_abort_if_worker_changed(self):
        prepared = self.prepare()
        with self.assertRaises(ObserverJournalError):
            self.open().abort_prepared(
                prepared=prepared, observed_worker_sha256=AFTER)
        self.assertTrue(self.open().state()["unresolved"])

    def test_signed_ack_and_checkpoint_survive_restart(self):
        prepared = self.prepare()
        cp, ack = self.ack(prepared)
        after = self.open()
        self.assertTrue(after.state()["unresolved"])
        self.assertEqual(after.state()["generation"], 2)
        self.assertEqual(after.state()["checkpoint_sha256"],
                         checkpoint_sha256(cp))
        self.assertTrue(verify_ack(
            ack, public_key=self.key.public_key(),
            fingerprint=public_fingerprint(self.key.public_key()),
            expected={
                "operation_id": OP, "request_sha256": REQUEST,
                "before_state_sha256": BEFORE, "after_state_sha256": AFTER,
                "generation": 2,
                "checkpoint_sha256": checkpoint_sha256(cp),
            }))
        with self.assertRaises(ObserverJournalError):
            after.prepare(
                operation_id=OP2, request_sha256=REQUEST,
                before_state_sha256=AFTER)
        after.confirm_delivery(ack=ack, observed_worker_sha256=AFTER)
        self.assertFalse(self.open().state()["unresolved"])
        next_prep = self.open().prepare(
            operation_id=OP2, request_sha256=REQUEST,
            before_state_sha256=AFTER)
        cp2, _ = self.open().acknowledge(
            prepared=next_prep,
            after_state_sha256=AFTER2,
            externally_observed_sha256=AFTER2)
        self.assertEqual(cp2["payload"]["generation"], 3)

    def test_duplicate_op_id_cannot_reissue(self):
        prepared = self.prepare()
        self.journal.abort_prepared(
            prepared=prepared, observed_worker_sha256=BEFORE)
        with self.assertRaises(ObserverJournalError):
            self.prepare()

    def test_lost_ack_prevents_second_operation(self):
        prepared = self.prepare()
        cp, ack = self.ack(prepared)
        with self.assertRaises(ObserverJournalError):
            self.open().prepare(
                operation_id=OP2, request_sha256=REQUEST,
                before_state_sha256=AFTER)
        with self.assertRaises(ObserverJournalError):
            self.open().abort_prepared(
                prepared=prepared, observed_worker_sha256=AFTER)
        self.assertTrue(self.open().state()["unresolved"])

    def test_tampered_ack_cannot_confirm(self):
        p = self.prepare()
        _, ack = self.ack(p)
        fake = {**ack, "payload": {
            **ack["payload"], "operation_id": OP2}}
        with self.assertRaises(ObserverJournalError):
            self.open().confirm_delivery(ack=fake,
                                         observed_worker_sha256=AFTER)
        self.assertTrue(self.open().state()["unresolved"])

    def test_wrong_worker_state_cannot_confirm(self):
        p = self.prepare()
        _, ack = self.ack(p)
        with self.assertRaises(ObserverJournalError):
            self.open().confirm_delivery(ack=ack,
                                         observed_worker_sha256=BEFORE)
        self.assertTrue(self.open().state()["unresolved"])

    def test_wrong_signer_key_denied(self):
        self.prepare()
        with self.assertRaises(ObserverJournalError):
            self.open(key=Ed25519PrivateKey.generate())

    def test_missing_file_fails_closed(self):
        self.path.unlink()
        with self.assertRaises(ObserverJournalError):
            self.open()
        self.assertFalse(self.path.exists())

    def test_existing_file_not_overwritten(self):
        self.prepare()
        with self.assertRaises(ObserverJournalError):
            self.open(create=True)

    def test_corrupted_db_fails_closed(self):
        self.path.write_bytes(b"corrupt")
        with self.assertRaises((ObserverJournalError, sqlite3.Error)):
            self.open()

    def test_symlink_file_fails_closed(self):
        replacement = self.root / "other.sqlite3"
        self.path.rename(replacement)
        self.path.symlink_to(replacement)
        with self.assertRaises(ObserverJournalError):
            self.open()

    def test_hardlinked_file_fails_closed(self):
        os.link(self.path, self.root / "alias")
        with self.assertRaises(ObserverJournalError):
            self.open()

    def test_owner_visible_file_fails_closed(self):
        os.chmod(self.path, 0o644)
        with self.assertRaises(ObserverJournalError):
            self.open()

    def test_untrusted_parent_fails_closed(self):
        os.chmod(self.root, 0o755)
        with self.assertRaises(ObserverJournalError):
            self.open()

    def test_mutated_signed_prepare_fails_validation(self):
        prepared = self.prepare()
        db = sqlite3.connect(str(self.path))
        db.execute(
            "UPDATE events SET prepare=? WHERE operation_id=?",
            ('{"invalid":true}', OP))
        db.commit()
        db.close()
        with self.assertRaises(ObserverJournalError):
            self.open()

    def test_mutated_signed_ack_fails_validation(self):
        prepared = self.prepare()
        self.ack(prepared)
        db = sqlite3.connect(str(self.path))
        db.execute(
            "UPDATE events SET ack=? WHERE operation_id=?",
            ('{"invalid":true}', OP))
        db.commit()
        db.close()
        with self.assertRaises(ObserverJournalError):
            self.open()

    def test_deleted_committed_event_denied_even_with_signed_highwater(self):
        prepared = self.prepare()
        _, ack = self.ack(prepared)
        self.open().confirm_delivery(ack=ack, observed_worker_sha256=AFTER)
        db = sqlite3.connect(self.path)
        db.execute("DELETE FROM events WHERE operation_id=?", (OP,))
        db.commit()
        db.close()
        with self.assertRaises(ObserverJournalError):
            self.open()

    def test_changed_commit_generation_denied(self):
        prepared = self.prepare()
        self.ack(prepared)
        db = sqlite3.connect(self.path)
        db.execute("UPDATE events SET generation=4 WHERE operation_id=?", (OP,))
        db.commit()
        db.close()
        with self.assertRaises(ObserverJournalError):
            self.open()

    def test_mutated_signed_checkpoint_denied(self):
        prepared = self.prepare()
        self.ack(prepared)
        db = sqlite3.connect(self.path)
        db.execute("UPDATE metadata SET checkpoint='{}'")
        db.commit()
        db.close()
        with self.assertRaises(ObserverJournalError):
            self.open()

    def test_unconfirmed_ack_refuses_reissue_across_restart(self):
        prepared = self.prepare()
        cp, ack = self.ack(prepared)
        for _ in range(3):
            reopened = self.open()
            with self.assertRaises(ObserverJournalError):
                reopened.acknowledge(
                    prepared=prepared, after_state_sha256=AFTER,
                    externally_observed_sha256=AFTER)
            self.assertTrue(self.open().state()["unresolved"])

    def test_outdated_db_image_remains_vulnerable(self):
        prepared = self.prepare()
        snapshot = self.root / "old-image"
        shutil.copy2(self.path, snapshot)
        cp, ack = self.ack(prepared)
        self.open().confirm_delivery(ack=ack, observed_worker_sha256=AFTER)
        shutil.copy2(snapshot, self.path)
        # This counterexample is a REQUIRED negative result. An old valid
        # DB still looks like a valid earlier observer state. Independent
        # monotonic custody is necessary before production.
        rolled = self.open()
        self.assertTrue(rolled.state()["unresolved"])
        self.assertEqual(rolled.state()["generation"], 1)
        self.assertEqual(rolled.state()["state_sha256"], BEFORE)

    def test_no_key_or_request_payload_persisted(self):
        p = self.prepare()
        raw = self.path.read_bytes()
        self.assertNotIn(b"synthetic request", raw)
        self.assertNotIn(b"synthetic worker state A", raw)
        self.assertFalse(self.journal.state()["observer_independence_verified"])
        self.assertFalse(self.journal.state()["rollback_resistance_verified"])


if __name__ == "__main__":
    unittest.main()
