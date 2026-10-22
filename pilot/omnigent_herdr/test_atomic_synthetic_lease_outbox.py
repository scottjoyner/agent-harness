"""V2 one-transaction debit and outbox controls, never a model gateway."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from durable_lease_store import DurableSyntheticLeaseGate, UnsafeLeaseState

KEY = hashlib.sha256(b"synthetic outbox-only signing key").digest()
HASH = hashlib.sha256(b"synthetic isolated worktree").hexdigest()
SCOPE = {
    "uid": 1000, "owner": "fixture-owner",
    "session": "fixture-session",
    "worktree_sha256": HASH,
    "model_pin": "kilo/kilo-auto/free",
}
EXPECTED = {
    "peer_uid": 1000,
    "expected_owner": "fixture-owner",
    "expected_session": "fixture-session",
    "expected_worktree_sha256": HASH,
    "expected_model_pin": "kilo/kilo-auto/free",
}
OP1 = "a" * 32
OP2 = "b" * 32
ZERO = "0" * 64


class AtomicSyntheticOutboxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="og-atomic-outbox-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        os.chmod(self.root, 0o700)
        self.db = self.root / "synthetic-v2.sqlite3"
        self.gate = self.open(create=True)
        self.token = self.gate.mint(**SCOPE)
        self.request = {"schema": 1, "op": "synthetic_receipt",
                        "token": self.token, "seq": 1}

    def open(self, *, create=False, key=KEY):
        return AtomicSyntheticLeaseOutbox(
            self.db, key, create=create, now=lambda: 1000)

    def execute(self, *, op=OP1, gate=None, request=None, **options):
        gate = gate or self.gate
        return gate.check(
            request or self.request, operation_id=op,
            **{**EXPECTED, **options})

    def inspect(self, *, gate=None, anchor=(0, ZERO)):
        return (gate or self.gate).inspect(trusted_anchor=anchor)

    def count(self):
        with sqlite3.connect(self.db) as db:
            return db.execute("SELECT count(*) FROM atomic_outbox").fetchone()[0]

    def used(self):
        with sqlite3.connect(self.db) as db:
            return db.execute("SELECT used FROM leases").fetchone()[0]

    def test_version_2_is_not_legacy_state(self):
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
        with self.assertRaises(UnsafeLeaseState):
            DurableSyntheticLeaseGate(self.db, KEY, now=lambda: 1000)

    def test_legacy_schema_cannot_be_opened_as_atomic(self):
        old_path = self.root / "legacy.sqlite3"
        DurableSyntheticLeaseGate(old_path, KEY, create=True, now=lambda: 1000)
        with self.assertRaises(UnsafeLeaseState):
            AtomicSyntheticLeaseOutbox(old_path, KEY, now=lambda: 1000)

    def test_missing_operation_id_denied_before_any_debit(self):
        result = self.gate.check(self.request, **EXPECTED)
        self.assertEqual(result["status"], "denied")
        self.assertEqual(self.used(), 0)
        self.assertEqual(self.count(), 0)

    def test_bogus_operation_id_denied(self):
        for bad in ("unexpected", "A" * 32, 9, True, "", "0" * 33):
            with self.subTest(value=bad):
                self.assertEqual(self.execute(op=bad)["status"], "denied")
        self.assertEqual(self.used(), 0)

    def test_atomic_debit_produces_one_authenticated_intent(self):
        result = self.execute()
        self.assertEqual(result["status"], "synthetic_receipt_only")
        self.assertEqual(result["receipt_number"], 1)
        self.assertEqual(result["atomic_outbox_record"], 1)
        self.assertEqual(self.used(), 1)
        self.assertEqual(self.count(), 1)
        after = self.inspect(anchor=(1, result["atomic_outbox_sha256"]))
        self.assertEqual(after["event_count"], 1)
        self.assertTrue(after["local_debit_intent_atomic"])
        self.assertFalse(after["outbox_published_to_observer"])
        self.assertFalse(after["agent_dispatch_authorized"])
        self.assertFalse(after["independent_anchor_custody_verified"])
        self.assertEqual(after["decision"], "HOLD")

    def test_restart_preserves_debit_and_intent(self):
        first = self.execute()
        reopened = self.open()
        self.assertEqual(self.used(), 1)
        self.assertEqual(reopened.inspect(
            trusted_anchor=(1, first["atomic_outbox_sha256"]))["event_count"], 1)
        self.assertEqual(self.execute(gate=reopened)["status"], "denied")
        self.assertEqual(self.used(), 1)

    def test_duplicate_operation_for_new_seq_denied(self):
        first = self.execute()
        next_request = {**self.request, "seq": 2}
        self.assertEqual(self.execute(op=OP1, request=next_request)["status"], "denied")
        self.assertEqual(self.used(), 1)
        self.assertEqual(self.count(), 1)

    def test_second_distinct_operation_has_chained_state_digest(self):
        first = self.execute()
        next_request = {**self.request, "seq": 2}
        second = self.execute(op=OP2, request=next_request)
        self.assertEqual(second["status"], "synthetic_receipt_only")
        self.assertEqual(second["receipt_number"], 2)
        self.assertEqual(self.used(), 2)
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.open().inspect(
            trusted_anchor=(2, second["atomic_outbox_sha256"]))["event_count"], 2)
        with sqlite3.connect(self.db) as db:
            rows = db.execute(
                "SELECT after_state_sha256,before_state_sha256,"
                "previous_record_sha256 FROM atomic_outbox ORDER BY record_no"
            ).fetchall()
        self.assertEqual(rows[0][0], rows[1][1])
        self.assertEqual(rows[1][2], first["atomic_outbox_sha256"])

    def test_injected_outbox_insert_failure_rolls_back_debit(self):
        initial = self.gate.state_digest()
        with patch.object(self.gate, "_commit_outbox",
                          side_effect=UnsafeLeaseState("disk write fault")):
            denied = self.execute()
        self.assertEqual(denied["status"], "denied")
        self.assertEqual(self.used(), 0)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.gate.state_digest(), initial)
        self.assertEqual(self.execute()["status"], "synthetic_receipt_only")

    def test_trigger_or_outbox_table_removed_fails_closed(self):
        with sqlite3.connect(self.db) as db:
            db.execute("DROP TABLE atomic_outbox")
        with self.assertRaises(sqlite3.Error):
            self.open()
        self.assertEqual(self.used(), 0)

    def test_deleted_outbox_row_is_rejected(self):
        self.execute()
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM atomic_outbox")
        with self.assertRaises(UnsafeLeaseState):
            self.open()

    def test_forged_record_hash_rejected(self):
        self.execute()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE atomic_outbox SET record_sha256=?", ("f" * 64,))
        with self.assertRaises(UnsafeLeaseState):
            self.open()

    def test_altered_keyed_hmac_rejected(self):
        self.execute()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE atomic_outbox SET hmac_sha256=?", ("a" * 64,))
        with self.assertRaises(UnsafeLeaseState):
            self.open()

    def test_outbox_replay_sequence_rejected(self):
        self.execute()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE atomic_outbox SET sequence=90")
        with self.assertRaises(UnsafeLeaseState):
            self.open()

    def test_key_change_rejected(self):
        self.execute()
        with self.assertRaises(UnsafeLeaseState):
            self.open(key=hashlib.sha256(b"wrong").digest())

    def test_wrong_external_anchor_refused(self):
        first = self.execute()
        with self.assertRaises(UnsafeLeaseState):
            self.gate.inspect(trusted_anchor=(0, ZERO))
        with self.assertRaises(UnsafeLeaseState):
            self.gate.inspect(trusted_anchor=(1, "f" * 64))
        with self.assertRaises(UnsafeLeaseState):
            self.gate.inspect(trusted_anchor=None)
        self.assertEqual(self.inspect(
            anchor=(1, first["atomic_outbox_sha256"]))["event_count"], 1)

    def test_old_valid_db_rejected_against_new_anchor(self):
        old = self.root / "old-valid-image"
        shutil.copy2(self.db, old)
        first = self.execute()
        shutil.copy2(old, self.db)
        with self.assertRaises(UnsafeLeaseState):
            self.gate.inspect(trusted_anchor=(1, first["atomic_outbox_sha256"]))

    def test_old_valid_db_and_old_anchor_still_pass(self):
        old = self.root / "old-valid-image"
        shutil.copy2(self.db, old)
        self.execute()
        shutil.copy2(old, self.db)
        self.assertEqual(self.inspect(anchor=(0, ZERO))["event_count"], 0)
        self.assertFalse(self.inspect(anchor=(0, ZERO))["rollback_resistance_verified"])

    def test_revoke_after_committed_intent_denied_as_unmodeled_state(self):
        self.execute()
        with self.assertRaises(UnsafeLeaseState):
            # Revocation is not part of V2's constrained outbox contract.
            self.gate.rotate_epoch()
        self.assertEqual(self.gate.state_digest(),
                         self.open().state_digest())

    def test_mint_after_outbox_intent_denied(self):
        self.execute()
        with self.assertRaises(UnsafeLeaseState):
            self.gate.mint(**{**SCOPE, "session": "second-session"})
        self.assertEqual(self.count(), 1)

    def test_expired_or_wrong_owner_never_creates_intent(self):
        wrong = self.execute(expected_owner="not-fixture-owner")
        self.assertEqual(wrong["status"], "denied")
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.used(), 0)

    def test_8_concurrent_same_operation_only_one_debit(self):
        barrier = threading.Barrier(8)
        replies = []
        lock = threading.Lock()

        def attempt():
            instance = self.open()
            barrier.wait(timeout=5)
            reply = self.execute(gate=instance)
            with lock:
                replies.append(reply["status"])

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        self.assertEqual(len(replies), 8)
        self.assertEqual(replies.count("synthetic_receipt_only"), 1)
        self.assertEqual(replies.count("denied"), 7)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.used(), 1)

    def test_corrupt_lease_debit_without_outbox_cannot_be_reopened(self):
        self.execute()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE leases SET used=0,last_seq=0")
        with self.assertRaises(UnsafeLeaseState):
            self.open()

    def test_no_plaintext_token_or_worker_key_written_to_outbox(self):
        self.execute()
        with sqlite3.connect(self.db) as db:
            fields = db.execute(
                "SELECT lease_jti,request_sha256,hmac_sha256 FROM atomic_outbox"
            ).fetchone()
        self.assertNotEqual(fields[0], self.token)
        self.assertNotEqual(fields[1], self.token)
        self.assertEqual(len(fields[2]), 64)


if __name__ == "__main__":
    unittest.main()
