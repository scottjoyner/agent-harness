"""Fail-closed restart/rollback tests for one synthetic V2 observer inbox."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from observer_checkpoint import ZERO, public_fingerprint
from v2_outbox_publication import (
    worker_publish, worker_sign_delivery,
)
from v2_durable_observer_inbox import (
    SyntheticV2DurableObserverInbox, UnsafeObserverInbox,
)

KEY = hashlib.sha256(b"synthetic durable inbox test HMAC").digest()
WORKTREE = hashlib.sha256(b"durable inbox worktree").hexdigest()
OPERATION = "a"*32


class DurableV2InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="og-v2-observer-inbox-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.workerdb = self.root / "worker-v2.sqlite3"
        self.inboxdb = self.root / "observer-inbox.sqlite3"
        self.worker = Ed25519PrivateKey.generate()
        self.observer = Ed25519PrivateKey.generate()
        self.worker_pin = public_fingerprint(self.worker.public_key())
        self.observer_pin = public_fingerprint(self.observer.public_key())
        self.gate = AtomicSyntheticLeaseOutbox(
            self.workerdb, KEY, now=lambda: 1000, create=True)
        token = self.gate.mint(
            uid=1000, owner="synthetic-owner",
            session="synthetic-session",
            worktree_sha256=WORKTREE,
            model_pin="kilo/kilo-auto/free")
        request = {"schema": 1, "op": "synthetic_receipt",
                   "token": token, "seq": 1}
        self.before = self.gate.state_digest()
        accepted = self.gate.check(
            request, operation_id=OPERATION, peer_uid=1000,
            expected_owner="synthetic-owner",
            expected_session="synthetic-session",
            expected_worktree_sha256=WORKTREE,
            expected_model_pin="kilo/kilo-auto/free")
        self.after = self.gate.state_digest()
        self.outbox_head = (1, accepted["atomic_outbox_sha256"])
        self.publication = worker_publish(
            signer=self.worker, worker_id="lenovo-fixture",
            observer_id="x1-fixture",
            observer_fingerprint=self.observer_pin,
            exported_head=self.gate.export_head_event(
                trusted_anchor=self.outbox_head))
        self.inbox = self.open(create=True)
        from v2_durable_observer_inbox import _digest
        with sqlite3.connect(self.inboxdb) as db:
            import json
            mark = json.loads(db.execute(
                "SELECT marker FROM metadata").fetchone()[0])
        self.genesis = (0, _digest(mark))

    def open(self, *, create=False, observer=None, worker=None):
        return SyntheticV2DurableObserverInbox(
            self.inboxdb,
            observer_signer=observer or self.observer,
            worker_public_key=(worker or self.worker).public_key(),
            observer_id="x1-fixture", worker_id="lenovo-fixture",
            initial_state=self.before if create else None,
            create=create)

    def receive(self, *, publication=None, anchor=None, op=OPERATION,
                before_head=None, after=None, inbox=None):
        return (inbox or self.inbox).receive(
            publication=self.publication if publication is None else publication,
            trusted_anchor=self.genesis if anchor is None else anchor,
            expected_operation_id=op,
            expected_outbox_head=self.outbox_head if before_head is None else before_head,
            expected_after_state=self.after if after is None else after)

    def signed_ack(self):
        received = self.receive()
        ackhead, ack = self.open().issue_ack(trusted_anchor=received)
        return received, ackhead, ack

    def delivery(self, ack):
        return worker_sign_delivery(
            signer=self.worker, publication=self.publication,
            observer_ack=ack, observer_public_key=self.observer.public_key(),
            trusted_observer_fingerprint=self.observer_pin,
            expected_generation=2, previous_checkpoint_sha256=ZERO)

    def finished(self):
        _, ackhead, ack = self.signed_ack()
        signed_delivery = self.delivery(ack)
        done = self.open().confirm(delivery=signed_delivery,
                                   trusted_anchor=ackhead)
        return ackhead, done, ack, signed_delivery

    def test_private_schema_and_non_authorizing_genesis(self):
        self.assertEqual(self.inboxdb.stat().st_mode & 0o777, 0o600)
        report = self.open().inspect(trusted_anchor=self.genesis)
        self.assertEqual(report["revision"], 0)
        self.assertEqual(report["stage"], "empty")
        self.assertFalse(report["agent_dispatch_authorized"])
        self.assertFalse(report["external_anchor_custody_verified"])

    def test_signed_publication_fence_survives_restart(self):
        head = self.receive()
        report = self.open().inspect(trusted_anchor=head)
        self.assertEqual(report["stage"], "publication_fenced")
        self.assertTrue(report["uncertain"])
        self.assertTrue(report["signed_publication_present"])
        self.assertFalse(report["signed_ack_present"])

    def test_missing_ack_delivery_fence_survives_restart(self):
        _, ackhead, _ = self.signed_ack()
        state = self.open().inspect(trusted_anchor=ackhead)
        self.assertEqual(state["stage"], "ack_signed_fenced")
        self.assertTrue(state["uncertain"])
        self.assertTrue(state["signed_ack_present"])
        self.assertFalse(state["signed_delivery_present"])

    def test_signed_delivery_completion_does_not_grant_models(self):
        _, final, _, _ = self.finished()
        state = self.open().inspect(trusted_anchor=final)
        self.assertEqual(state["stage"], "delivered")
        self.assertFalse(state["uncertain"])
        self.assertTrue(state["signed_delivery_present"])
        self.assertFalse(state["agent_dispatch_authorized"])
        self.assertFalse(state["source_transaction_attested"])

    def test_repeated_publication_denied(self):
        head = self.receive()
        with self.assertRaises(UnsafeObserverInbox):
            self.receive(anchor=head)

    def test_lost_ack_not_reissued(self):
        head = self.receive()
        newer, _ = self.open().issue_ack(trusted_anchor=head)
        for _ in range(3):
            with self.assertRaises(UnsafeObserverInbox):
                self.open().issue_ack(trusted_anchor=newer)
        self.assertTrue(self.open().inspect(trusted_anchor=newer)["uncertain"])

    def test_ack_with_old_head_denied(self):
        self.receive()
        with self.assertRaises(UnsafeObserverInbox):
            self.open().issue_ack(trusted_anchor=self.genesis)

    def test_wrong_worker_key_rejected_on_restart(self):
        with self.assertRaises(UnsafeObserverInbox):
            self.open(worker=Ed25519PrivateKey.generate())

    def test_wrong_observer_key_rejected_on_restart(self):
        with self.assertRaises(UnsafeObserverInbox):
            self.open(observer=Ed25519PrivateKey.generate())

    def test_stale_head_on_inspection_denied(self):
        head = self.receive()
        with self.assertRaises(UnsafeObserverInbox):
            self.open().inspect(trusted_anchor=self.genesis)
        with self.assertRaises(UnsafeObserverInbox):
            self.open().inspect(trusted_anchor=(1, "f"*64))
        self.assertEqual(self.open().inspect(trusted_anchor=head)["revision"], 1)

    def test_missing_head_on_inspection_denied(self):
        with self.assertRaises(UnsafeObserverInbox):
            self.open().inspect(trusted_anchor=None)

    def test_wrong_operation_denied_without_writing(self):
        with self.assertRaises(UnsafeObserverInbox):
            self.receive(op="b"*32)
        self.assertEqual(self.open().inspect(
            trusted_anchor=self.genesis)["stage"], "empty")

    def test_wrong_outbox_head_denied_without_writing(self):
        with self.assertRaises(UnsafeObserverInbox):
            self.receive(before_head=(2, self.outbox_head[1]))
        self.assertEqual(self.open().inspect(
            trusted_anchor=self.genesis)["revision"], 0)

    def test_wrong_expected_state_denied_without_writing(self):
        with self.assertRaises(UnsafeObserverInbox):
            self.receive(after="f"*64)
        self.assertEqual(self.open().inspect(
            trusted_anchor=self.genesis)["revision"], 0)

    def test_forged_publication_denied(self):
        bad = deepcopy(self.publication)
        bad["payload"]["outbox_record_sha256"] = "f"*64
        with self.assertRaises(UnsafeObserverInbox):
            self.receive(publication=bad)
        self.assertEqual(self.open().inspect(
            trusted_anchor=self.genesis)["revision"], 0)

    def test_tampered_publication_quarantine_persists_restart(self):
        altered = deepcopy(self.publication)
        altered["payload"]["outbox_record_sha256"] = "f"*64
        with self.assertRaises(UnsafeObserverInbox):
            self.receive(publication=altered)
        held = self.inbox.quarantine_unverified(
            claimed_publication=altered,
            expected_operation_id=OPERATION,
            trusted_anchor=self.genesis)
        state = self.open().inspect(trusted_anchor=held)
        self.assertEqual(state["stage"], "quarantined_untrusted")
        self.assertTrue(state["uncertain"])
        self.assertFalse(state["signed_publication_present"])
        with self.assertRaises(UnsafeObserverInbox):
            self.open().issue_ack(trusted_anchor=held)
        with self.assertRaises(UnsafeObserverInbox):
            self.receive(anchor=held)

    def test_duplicate_quarantine_cannot_overwrite_first_evidence(self):
        held = self.inbox.quarantine_unverified(
            claimed_publication=self.publication,
            expected_operation_id=OPERATION,
            trusted_anchor=self.genesis)
        with self.assertRaises(UnsafeObserverInbox):
            self.open().quarantine_unverified(
                claimed_publication={"tampered": "new"},
                expected_operation_id=OPERATION,
                trusted_anchor=held)
        self.assertEqual(self.open().inspect(
            trusted_anchor=held)["stage"], "quarantined_untrusted")

    def test_quarantine_only_valid_operation_scope(self):
        with self.assertRaises(UnsafeObserverInbox):
            self.inbox.quarantine_unverified(
                claimed_publication={}, expected_operation_id="bad",
                trusted_anchor=self.genesis)
        self.assertEqual(self.open().inspect(
            trusted_anchor=self.genesis)["stage"], "empty")

    def test_quarantine_database_rollback_detected_by_newer_head(self):
        snapshot = self.root / "pre-quarantine-image"
        shutil.copy2(self.inboxdb, snapshot)
        held = self.inbox.quarantine_unverified(
            claimed_publication={"invalid": "signed-status"},
            expected_operation_id=OPERATION,
            trusted_anchor=self.genesis)
        shutil.copy2(snapshot, self.inboxdb)
        with self.assertRaises(UnsafeObserverInbox):
            self.open().inspect(trusted_anchor=held)
        # Negative: attacker restoring the stale anchor too still wins.
        self.assertEqual(self.open().inspect(
            trusted_anchor=self.genesis)["stage"], "empty")

    def test_missing_delivery_cannot_clear_fence(self):
        _, ackhead, _ = self.signed_ack()
        with self.assertRaises(UnsafeObserverInbox):
            self.open().confirm(delivery={}, trusted_anchor=ackhead)
        self.assertTrue(self.open().inspect(
            trusted_anchor=ackhead)["uncertain"])

    def test_tampered_delivery_cannot_clear_fence(self):
        _, ackhead, ack = self.signed_ack()
        forged = deepcopy(self.delivery(ack))
        forged["payload"]["observer_ack_sha256"] = "f"*64
        with self.assertRaises(UnsafeObserverInbox):
            self.open().confirm(delivery=forged, trusted_anchor=ackhead)
        self.assertTrue(self.open().inspect(
            trusted_anchor=ackhead)["uncertain"])

    def test_replay_delivery_after_confirm_denied(self):
        _, final, _, receipt = self.finished()
        with self.assertRaises(UnsafeObserverInbox):
            self.open().confirm(delivery=receipt, trusted_anchor=final)

    def test_two_concurrent_ack_issuers_only_one_wins(self):
        head = self.receive()
        barrier = threading.Barrier(6)
        result = []
        guard = threading.Lock()
        def attempt():
            barrier.wait(timeout=5)
            try:
                self.open().issue_ack(trusted_anchor=head)
                status = "signed"
            except (UnsafeObserverInbox, sqlite3.Error):
                status = "denied"
            with guard:
                result.append(status)
        threads = [threading.Thread(target=attempt) for _ in range(6)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=8)
        self.assertEqual(result.count("signed"), 1)
        self.assertEqual(result.count("denied"), 5)

    def test_signed_ack_write_failure_rolled_back(self):
        head = self.receive()
        with patch.object(self.inbox, "_save",
                          side_effect=UnsafeObserverInbox("fault before marker")):
            with self.assertRaises(UnsafeObserverInbox):
                self.inbox.issue_ack(trusted_anchor=head)
        state = self.open().inspect(trusted_anchor=head)
        self.assertEqual(state["stage"], "publication_fenced")
        self.assertFalse(state["signed_ack_present"])

    def test_signed_delivery_write_failure_rolled_back(self):
        _, head, ack = self.signed_ack()
        signed = self.delivery(ack)
        with patch.object(self.inbox, "_save",
                          side_effect=UnsafeObserverInbox("fault before marker")):
            with self.assertRaises(UnsafeObserverInbox):
                self.inbox.confirm(delivery=signed, trusted_anchor=head)
        state = self.open().inspect(trusted_anchor=head)
        self.assertEqual(state["stage"], "ack_signed_fenced")
        self.assertFalse(state["signed_delivery_present"])

    def test_deleted_publication_rejected_on_reopen(self):
        self.receive()
        with sqlite3.connect(self.inboxdb) as db:
            db.execute("DELETE FROM journal")
        with self.assertRaises(UnsafeObserverInbox):
            self.open()

    def test_tampered_persisted_ack_rejected(self):
        self.signed_ack()
        with sqlite3.connect(self.inboxdb) as db:
            db.execute("UPDATE journal SET signed_ack='{}'")
        with self.assertRaises(UnsafeObserverInbox):
            self.open()

    def test_tampered_signed_stage_marker_rejected(self):
        self.receive()
        with sqlite3.connect(self.inboxdb) as db:
            db.execute("UPDATE metadata SET marker='{}'")
        with self.assertRaises(UnsafeObserverInbox):
            self.open()

    def test_old_valid_db_rejected_against_newer_anchor(self):
        old = self.root / "old-valid-inbox"
        shutil.copy2(self.inboxdb, old)
        head = self.receive()
        shutil.copy2(old, self.inboxdb)
        with self.assertRaises(UnsafeObserverInbox):
            self.open().inspect(trusted_anchor=head)

    def test_old_db_plus_old_anchor_still_accepted(self):
        old = self.root / "old-valid-inbox"
        shutil.copy2(self.inboxdb, old)
        self.receive()
        shutil.copy2(old, self.inboxdb)
        self.assertEqual(self.open().inspect(
            trusted_anchor=self.genesis)["stage"], "empty")
        self.assertFalse(self.open().inspect(
            trusted_anchor=self.genesis)["rollback_resistance_verified"])

    def test_symlink_and_hardlink_rejected(self):
        os.link(self.inboxdb, self.root / "alias")
        with self.assertRaises(UnsafeObserverInbox):
            self.open()
        (self.root / "alias").unlink()
        self.inboxdb.rename(self.root / "original")
        self.inboxdb.symlink_to(self.root / "original")
        with self.assertRaises(UnsafeObserverInbox):
            self.open()

    def test_world_readable_database_rejected(self):
        os.chmod(self.inboxdb, 0o644)
        with self.assertRaises(UnsafeObserverInbox):
            self.open()

    def test_missing_database_fails_without_silent_reset(self):
        self.inboxdb.unlink()
        with self.assertRaises(UnsafeObserverInbox):
            self.open()

    def test_existing_database_cannot_be_recreated(self):
        with self.assertRaises(UnsafeObserverInbox):
            self.open(create=True)


if __name__ == "__main__":
    unittest.main()
