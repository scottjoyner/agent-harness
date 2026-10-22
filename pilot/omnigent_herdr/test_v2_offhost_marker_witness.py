"""V2 observer marker off-host chain, restart, rollback and custody limits."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from observer_checkpoint import ZERO, public_fingerprint
from v2_durable_observer_inbox import (
    SyntheticV2DurableObserverInbox, UnsafeObserverInbox,
)
from v2_outbox_publication import worker_publish, worker_sign_delivery
from v2_offhost_marker_witness import (
    V2OffhostMarkerWitness, UnsafeOffhostWitness,
)

WORKTREE = hashlib.sha256(b"v2 offhost witness test").hexdigest()
OP = "a"*32
KEY = hashlib.sha256(b"offhost synthetic HMAC").digest()


class OffhostMarkerTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="og-v2-offhost-witness-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        os.chmod(self.root, 0o700)
        self.x1 = self.root / "x1"
        self.remote = self.root / "offhost"
        self.x1.mkdir(mode=0o700)
        self.remote.mkdir(mode=0o700)
        self.observer = Ed25519PrivateKey.generate()
        self.worker = Ed25519PrivateKey.generate()
        self.observer_pin = public_fingerprint(self.observer.public_key())
        self.worker_pin = public_fingerprint(self.worker.public_key())
        self.workerdb = self.x1 / "worker.sqlite3"
        self.inboxdb = self.x1 / "observer.sqlite3"
        self.logpath = self.remote / "marker-witness.jsonl"
        self.gate = AtomicSyntheticLeaseOutbox(
            self.workerdb, KEY, now=lambda: 1000, create=True)
        token = self.gate.mint(
            uid=1000, owner="research-owner", session="research-session",
            worktree_sha256=WORKTREE, model_pin="kilo/kilo-auto/free")
        self.before = self.gate.state_digest()
        req = {"schema": 1, "op": "synthetic_receipt",
               "token": token, "seq": 1}
        accepted = self.gate.check(
            req, operation_id=OP, peer_uid=1000,
            expected_owner="research-owner",
            expected_session="research-session",
            expected_worktree_sha256=WORKTREE,
            expected_model_pin="kilo/kilo-auto/free")
        self.after = self.gate.state_digest()
        self.outbox_head = (1, accepted["atomic_outbox_sha256"])
        self.pub = worker_publish(
            signer=self.worker, worker_id="worker",
            observer_id="observer", observer_fingerprint=self.observer_pin,
            exported_head=self.gate.export_head_event(
                trusted_anchor=self.outbox_head))
        self.inbox = SyntheticV2DurableObserverInbox(
            self.inboxdb, observer_signer=self.observer,
            worker_public_key=self.worker.public_key(),
            observer_id="observer", worker_id="worker",
            create=True, initial_state=self.before)
        self.g = self.inbox.created_head
        self.witness = self.witness_open()
        self.m0 = self.inbox.export_signed_marker(trusted_anchor=self.g)
        self.w0 = self.witness.append(self.m0, trusted_anchor=None, genesis=True)

    def inbox_open(self):
        return SyntheticV2DurableObserverInbox(
            self.inboxdb, observer_signer=self.observer,
            worker_public_key=self.worker.public_key(),
            observer_id="observer", worker_id="worker")

    def witness_open(self, *, observer=None, worker_pin=None):
        return V2OffhostMarkerWitness(
            self.logpath,
            observer_public_key=(observer or self.observer).public_key(),
            observer_id="observer", worker_id="worker",
            worker_pin=worker_pin or self.worker_pin)

    def stage1(self):
        i1 = self.inbox.receive(
            publication=self.pub, trusted_anchor=self.g,
            expected_operation_id=OP,
            expected_outbox_head=self.outbox_head,
            expected_after_state=self.after)
        m1 = self.inbox_open().export_signed_marker(trusted_anchor=i1)
        w1 = self.witness_open().append(m1, trusted_anchor=self.w0)
        return i1, w1, m1

    def stage2(self):
        i1, w1, m1 = self.stage1()
        i2, ack = self.inbox_open().issue_ack(trusted_anchor=i1)
        m2 = self.inbox_open().export_signed_marker(trusted_anchor=i2)
        w2 = self.witness_open().append(m2, trusted_anchor=w1)
        return i2, w2, ack, m2

    def stage3(self):
        i2, w2, ack, _ = self.stage2()
        delivery = worker_sign_delivery(
            signer=self.worker, publication=self.pub, observer_ack=ack,
            observer_public_key=self.observer.public_key(),
            trusted_observer_fingerprint=self.observer_pin,
            expected_generation=2, previous_checkpoint_sha256=ZERO)
        i3 = self.inbox_open().confirm(
            delivery=delivery, trusted_anchor=i2)
        m3 = self.inbox_open().export_signed_marker(trusted_anchor=i3)
        w3 = self.witness_open().append(m3, trusted_anchor=w2)
        return i3, w3, m3

    def compare(self, local_anchor, witness_anchor):
        local = self.inbox_open().inspect(trusted_anchor=local_anchor)
        remote = self.witness_open().inspect(
            trusted_anchor=witness_anchor,
            expected_marker_head=local_anchor)
        return local, remote

    def test_genesis_is_signed_read_only_and_no_dispatch(self):
        local, remote = self.compare(self.g, self.w0)
        self.assertEqual(local["stage"], "empty")
        self.assertEqual(remote["records"], 1)
        self.assertFalse(remote["independent_administrator_verified"])
        self.assertFalse(remote["agent_dispatch_authorized"])
        self.assertEqual(remote["decision"], "HOLD")
        self.assertEqual(self.logpath.stat().st_mode & 0o777, 0o600)

    def test_all_four_stages_verified_across_readonly_reopens(self):
        i3, w3, m3 = self.stage3()
        local, remote = self.compare(i3, w3)
        self.assertEqual(local["stage"], "delivered")
        self.assertEqual(remote["marker_stage"], "delivered")
        self.assertEqual(remote["records"], 4)
        self.assertEqual(remote["marker_sha256"], i3[1])
        self.assertFalse(local["agent_dispatch_authorized"])

    def test_pending_ack_stays_fenced_on_both_hosts(self):
        i2, w2, _ack, _m2 = self.stage2()
        local, remote = self.compare(i2, w2)
        self.assertTrue(local["uncertain"])
        self.assertEqual(remote["marker_stage"], "ack_signed_fenced")
        with self.assertRaises(UnsafeObserverInbox):
            self.inbox_open().issue_ack(trusted_anchor=i2)

    def test_offhost_witness_detects_older_observer_db(self):
        old = self.x1 / "snapshot_before_ack"
        i1, w1, _ = self.stage1()
        shutil.copy2(self.inboxdb, old)
        i2, w2, _, _ = self._finish_ack(i1, w1)
        shutil.copy2(old, self.inboxdb)
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().inspect(
                trusted_anchor=w2, expected_marker_head=i1)
        with self.assertRaises(UnsafeObserverInbox):
            self.inbox_open().inspect(trusted_anchor=i2)
        self.assertEqual(self.witness_open().inspect(
            trusted_anchor=w2, expected_marker_head=i2)["marker_revision"], 2)

    def _finish_ack(self, i1, w1):
        i2, ack = self.inbox_open().issue_ack(trusted_anchor=i1)
        marker = self.inbox_open().export_signed_marker(trusted_anchor=i2)
        w2 = self.witness_open().append(marker, trusted_anchor=w1)
        return i2, w2, ack, marker

    def test_offhost_log_rollback_denied_with_newer_trusted_head(self):
        previous = self.remote / "previous-witness"
        shutil.copy2(self.logpath, previous)
        i1, w1, _ = self.stage1()
        shutil.copy2(previous, self.logpath)
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().inspect(
                trusted_anchor=w1, expected_marker_head=i1)
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().append(
                self.inbox_open().export_signed_marker(trusted_anchor=i1),
                trusted_anchor=w1)

    def test_old_observer_and_old_witness_with_old_heads_is_still_attack(self):
        old_inbox = self.x1 / "old-inbox"
        old_log = self.remote / "old-log"
        shutil.copy2(self.inboxdb, old_inbox)
        shutil.copy2(self.logpath, old_log)
        self.stage3()
        shutil.copy2(old_inbox, self.inboxdb)
        shutil.copy2(old_log, self.logpath)
        local, offhost = self.compare(self.g, self.w0)
        self.assertEqual(local["stage"], "empty")
        self.assertEqual(offhost["marker_stage"], "empty")
        self.assertFalse(offhost["rollback_resistance_verified"])

    def test_missing_anchor_denied(self):
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().inspect(
                trusted_anchor=None, expected_marker_head=self.g)
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().append(self.m0, trusted_anchor=self.w0)

    def test_skip_ack_stage_denied_even_if_marker_signed(self):
        i1, w1, _ = self.stage1()
        unsigned = self.inbox._mark(
            revision=3, stage="delivered", operation_id=OP,
            publication_sha256=self.pub["payload"]["outbox_record_sha256"],
            ack_sha256="1"*64, delivery_sha256="2"*64,
            before_state_sha256=self.before,
            after_state_sha256=self.after,
            outbox_record_no=1, outbox_record_sha256=self.outbox_head[1])
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().append(unsigned, trusted_anchor=w1)

    def test_wrong_signing_key_denied(self):
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open(observer=Ed25519PrivateKey.generate()).inspect(
                trusted_anchor=self.w0, expected_marker_head=self.g)

    def test_altered_signature_rejected(self):
        i1, w1, _ = self.stage1()
        fake = deepcopy(self.inbox_open().export_signed_marker(trusted_anchor=i1))
        fake["payload"]["after_state_sha256"] = "f"*64
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().append(fake, trusted_anchor=w1)

    def test_signed_quarantine_published_as_terminal_witness(self):
        quarantined = self.inbox.quarantine_unverified(
            claimed_publication={"unverified": True},
            expected_operation_id=OP, trusted_anchor=self.g)
        marker = self.inbox_open().export_signed_marker(
            trusted_anchor=quarantined)
        wq = self.witness_open().append(marker, trusted_anchor=self.w0)
        self.assertEqual(self.witness_open().inspect(
            trusted_anchor=wq, expected_marker_head=quarantined)[
                "marker_stage"], "quarantined_untrusted")
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().append(marker, trusted_anchor=wq)

    def test_concurrent_same_head_only_one_append(self):
        i1 = self.inbox.receive(
            publication=self.pub, trusted_anchor=self.g,
            expected_operation_id=OP,
            expected_outbox_head=self.outbox_head,
            expected_after_state=self.after)
        m1 = self.inbox.export_signed_marker(trusted_anchor=i1)
        barrier = threading.Barrier(6)
        results = []
        lock = threading.Lock()
        def append():
            barrier.wait(timeout=7)
            try:
                self.witness_open().append(m1, trusted_anchor=self.w0)
                result = "accepted"
            except UnsafeOffhostWitness:
                result = "denied"
            with lock:
                results.append(result)
        threads = [threading.Thread(target=append) for _ in range(6)]
        for th in threads: th.start()
        for th in threads: th.join(timeout=9)
        self.assertEqual(results.count("accepted"), 1)
        self.assertEqual(results.count("denied"), 5)

    def test_truncated_record_denied(self):
        self.logpath.write_bytes(self.logpath.read_bytes()[:-1])
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().inspect(
                trusted_anchor=self.w0, expected_marker_head=self.g)

    def test_missing_log_denied_without_reset(self):
        self.logpath.unlink()
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().inspect(
                trusted_anchor=self.w0, expected_marker_head=self.g)

    def test_invalid_permissions_denied(self):
        os.chmod(self.logpath, 0o644)
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().inspect(
                trusted_anchor=self.w0, expected_marker_head=self.g)

    def test_linked_witness_denied(self):
        os.link(self.logpath, self.remote / "hardlink")
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().inspect(
                trusted_anchor=self.w0, expected_marker_head=self.g)

    def test_replay_signed_genesis_denied(self):
        with self.assertRaises(UnsafeOffhostWitness):
            self.witness_open().append(self.m0, trusted_anchor=self.w0)


if __name__ == "__main__":
    unittest.main()
