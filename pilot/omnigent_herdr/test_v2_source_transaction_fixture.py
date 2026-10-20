"""Synthetic source-side SQLite commit, signature and cross-signed intent."""
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

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from observer_checkpoint import ZERO, public_fingerprint
from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from v2_outbox_publication import worker_publish
from v2_source_transaction_fixture import (
    SyntheticResourceSource, UnsafeSourceState, _state_digest,
    _unique_fields, verify_source_receipt,
    verify_stale_source_fence,
    verify_source_corresponds_to_signed_worker_intent,
)

OP1 = "1"*32
OP2 = "2"*32
INTENT1 = hashlib.sha256(b"synthetic-source-intent-1").hexdigest()
INTENT2 = hashlib.sha256(b"synthetic-source-intent-2").hexdigest()


class SourceTransactionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="og-v2-source-tst-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.cache = self.home / ".cache"
        self.cache.mkdir(mode=0o700)
        self.root = self.cache / "og-v2-source-Ab91zQ"
        self.root.mkdir(mode=0o700)
        home_patch = patch.object(Path, "home", return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.source = SyntheticResourceSource(self.root, create=True)
        self.pin = public_fingerprint(self.source.signer.public_key())
        self.genesis = (0, ZERO)

    def reopen(self):
        return SyntheticResourceSource(self.root)

    def committed(self, *, operation_id=OP1, intent=INTENT1, head=None):
        return self.reopen().commit(
            operation_id=operation_id, intent_sha256=intent,
            trusted_head=self.genesis if head is None else head)

    def verify(self, response, *, operation=OP1, intent=INTENT1,
               key=None, pin=None, head=None, before=None, after=None):
        return verify_source_receipt(
            response["receipt"],
            source_public_key=key or self.source.signer.public_key(),
            expected_source_pin=pin or self.pin,
            expected_operation=operation,
            expected_intent_sha256=intent,
            expected_head=tuple(response["head"]) if head is None else head,
            expected_before_sha256=(
                _state_digest(2, 0, ZERO) if before is None else before),
            expected_after_sha256=(
                _state_digest(1, 1, ZERO) if after is None else after),
        )

    def test_bootstrap_is_private_and_empty(self):
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / "source-ephemeral.key").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "source-debits.sqlite3").stat().st_mode & 0o777, 0o600)
        snap = self.reopen().inspect(trusted_head=self.genesis)
        self.assertEqual(snap["balance"], 2)
        self.assertEqual(snap["head"], [0, ZERO])
        self.assertIsNone(snap["receipt"])
        self.assertFalse(snap["independently_administered_source_verified"])
        self.assertFalse(snap["agent_dispatch_authorized"])

    def test_local_committed_debit_is_signed_and_restart_readable(self):
        result = self.committed()
        self.assertEqual(result["balance"], 1)
        self.assertTrue(self.verify(result))
        self.assertEqual(self.reopen().inspect(
            trusted_head=tuple(result["head"]))["receipt"], result["receipt"])
        with sqlite3.connect(self.root / "source-debits.sqlite3") as db:
            self.assertEqual(db.execute("SELECT count(*) FROM debits").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT balance FROM resource").fetchone()[0], 1)

    def test_duplicate_source_operation_refuses_second_local_debit(self):
        result = self.committed()
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=OP1, head=tuple(result["head"]))
        self.assertEqual(self.reopen().inspect(
            trusted_head=tuple(result["head"]))["balance"], 1)

    def test_stale_source_head_never_commits(self):
        result = self.committed()
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=OP2, intent=INTENT2)
        self.assertTrue(self.verify(result))

    def test_valid_second_commit_and_capacity_two(self):
        first = self.committed()
        second = self.committed(operation_id=OP2, intent=INTENT2,
                                head=tuple(first["head"]))
        self.assertEqual(second["balance"], 0)
        self.assertEqual(second["receipt"]["payload"]["previous_record_sha256"],
                         first["head"][1])
        self.assertTrue(self.verify(
            second, operation=OP2, intent=INTENT2,
            before=_state_digest(1, 1, first["head"][1]),
            after=_state_digest(0, 2, first["head"][1])))
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id="3"*32, intent=INTENT2,
                           head=tuple(second["head"]))

    def test_one_parallel_source_commit_per_genesis_head(self):
        barrier = threading.Barrier(5)
        outcomes = []
        lock = threading.Lock()
        def run(i):
            barrier.wait(timeout=6)
            try:
                self.committed(operation_id=f"{i:032x}")
                x = "committed"
            except (UnsafeSourceState, sqlite3.Error):
                x = "denied"
            with lock:
                outcomes.append(x)
        ths = [threading.Thread(target=run, args=(i,)) for i in range(5)]
        for t in ths: t.start()
        for t in ths: t.join(timeout=8)
        self.assertEqual(outcomes.count("committed"), 1)
        self.assertEqual(outcomes.count("denied"), 4)

    def test_forged_source_receipt_signature_fails(self):
        result = self.committed()
        forged = deepcopy(result)
        forged["receipt"]["payload"]["operation_id"] = OP2
        self.assertFalse(self.verify(forged))
        forged = deepcopy(result)
        forged["receipt"]["signature"] = "AAAA"+forged["receipt"]["signature"][4:]
        self.assertFalse(self.verify(forged))

    def test_wrong_source_public_key_fails(self):
        result = self.committed()
        self.assertFalse(self.verify(
            result, key=Ed25519PrivateKey.generate().public_key()))
        self.assertFalse(self.verify(result, pin="f"*64))

    def test_wrong_operation_and_intent_fails(self):
        result = self.committed()
        self.assertFalse(self.verify(result, operation=OP2))
        self.assertFalse(self.verify(result, intent=INTENT2))

    def test_stale_expected_head_and_state_fails(self):
        result = self.committed()
        self.assertFalse(self.verify(result, head=(0, ZERO)))
        self.assertFalse(self.verify(result, after="f"*64))

    def test_corrupt_source_balance_denied_after_reopen(self):
        self.committed()
        with sqlite3.connect(self.source.dbpath) as db:
            db.execute("UPDATE resource SET balance=0 WHERE id=1")
        with self.assertRaises(UnsafeSourceState):
            self.reopen().inspect(trusted_head=(0, ZERO))

    def test_deleted_receipt_fails_source_replay_check(self):
        self.committed()
        with sqlite3.connect(self.source.dbpath) as db:
            db.execute("DELETE FROM debits")
        with self.assertRaises(UnsafeSourceState):
            self.reopen().inspect(trusted_head=(0, ZERO))

    def test_changed_saved_signature_denied(self):
        self.committed()
        with sqlite3.connect(self.source.dbpath) as db:
            db.execute("UPDATE debits SET receipt_json='{}' WHERE record_no=1")
        with self.assertRaises(UnsafeSourceState):
            self.reopen().inspect(trusted_head=(0, ZERO))

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(UnsafeSourceState):
            _unique_fields([("op", 1), ("op", 2)])

    def test_missing_signing_key_fails_closed(self):
        result = self.committed()
        (self.root / "source-ephemeral.key").unlink()
        with self.assertRaises((UnsafeSourceState, OSError)):
            self.reopen()
        self.assertEqual(result["balance"], 1)

    def test_signer_key_symlink_denied(self):
        key = self.root / "source-ephemeral.key"
        key.rename(self.root / "original.key")
        key.symlink_to(self.root / "original.key")
        with self.assertRaises(UnsafeSourceState):
            self.reopen()

    def test_unsafe_db_hardlink_denied(self):
        os.link(self.source.dbpath, self.root / "otherdb")
        with self.assertRaises(UnsafeSourceState):
            self.reopen().inspect(trusted_head=self.genesis)

    def test_missing_db_does_not_recreate(self):
        self.source.dbpath.unlink()
        with self.assertRaises((UnsafeSourceState, OSError)):
            self.reopen().inspect(trusted_head=self.genesis)

    def test_duplicate_bootstrap_refused(self):
        with self.assertRaises(FileExistsError):
            SyntheticResourceSource(self.root, create=True)

    def test_old_valid_source_db_plus_old_head_still_accepted(self):
        result = self.committed()
        snapshot = self.root / "valid-old.sqlite3"
        shutil.copy2(self.source.dbpath, snapshot)
        second = self.committed(operation_id=OP2, intent=INTENT2,
                                head=tuple(result["head"]))
        shutil.copy2(snapshot, self.source.dbpath)
        with self.assertRaises(UnsafeSourceState):
            self.reopen().inspect(trusted_head=tuple(second["head"]))
        self.assertEqual(self.reopen().inspect(
            trusted_head=tuple(result["head"]))["balance"], 1)

    def test_failed_balance_write_rolls_back_receipt_insert(self):
        # Inject an SQLite transactional fault AFTER INSERT debits but
        # BEFORE resource count/balance update. SQLite must roll back
        # both writes rather than leaving a source-signed phantom debit.
        with sqlite3.connect(self.source.dbpath) as db:
            db.execute("""CREATE TRIGGER abort_balance_change
                BEFORE UPDATE ON resource
                BEGIN
                    SELECT RAISE(ABORT, 'injected source balance fault');
                END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.committed()
        with sqlite3.connect(self.source.dbpath) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM debits").fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT balance,count,last_sha256 FROM resource").fetchone(),
                (2, 0, ZERO))
        self.assertEqual(self.reopen().inspect(
            trusted_head=self.genesis)["balance"], 2)

    def test_replaced_source_private_key_rejects_valid_old_db(self):
        first = self.committed()
        oldkey = self.root / "source-ephemeral.key"
        replacement = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
        oldkey.write_bytes(replacement)
        with self.assertRaises(UnsafeSourceState):
            self.reopen().inspect(trusted_head=tuple(first["head"]))

    def test_read_only_lost_reply_recovery_requires_previous_head(self):
        committed = self.committed()
        recovered = self.reopen().recover_committed_operation(
            operation_id=OP1, intent_sha256=INTENT1,
            trusted_prior_head=self.genesis)
        self.assertEqual(recovered["mode"],
                         "synthetic_source_read_only_recovery")
        self.assertEqual(recovered["receipt"], committed["receipt"])
        self.assertEqual(recovered["head"], committed["head"])
        self.assertFalse(recovered["agent_dispatch_authorized"])
        self.assertEqual(self.reopen().inspect(
            trusted_head=tuple(committed["head"]))["balance"], 1)

    def test_absent_effect_recovery_refused_not_safe_retry(self):
        with self.assertRaises(UnsafeSourceState):
            self.reopen().recover_committed_operation(
                operation_id=OP1, intent_sha256=INTENT1,
                trusted_prior_head=self.genesis)
        self.assertEqual(self.reopen().inspect(
            trusted_head=self.genesis)["balance"], 2)

    def test_wrong_prior_anchor_never_recovers_signed_receipt(self):
        committed = self.committed()
        with self.assertRaises(UnsafeSourceState):
            self.reopen().recover_committed_operation(
                operation_id=OP1, intent_sha256=INTENT1,
                trusted_prior_head=(0, "f"*64))
        self.assertTrue(self.verify(committed))

    def test_wrong_operation_or_digest_never_recovers(self):
        self.committed()
        for op, intent in ((OP2, INTENT1), (OP1, INTENT2)):
            with self.subTest(operation=op, intent=intent):
                with self.assertRaises(UnsafeSourceState):
                    self.reopen().recover_committed_operation(
                        operation_id=op, intent_sha256=intent,
                        trusted_prior_head=self.genesis)

    def test_only_latest_source_effect_is_eligible_for_recovery(self):
        first = self.committed()
        self.committed(operation_id=OP2, intent=INTENT2,
                       head=tuple(first["head"]))
        with self.assertRaises(UnsafeSourceState):
            self.reopen().recover_committed_operation(
                operation_id=OP1, intent_sha256=INTENT1,
                trusted_prior_head=self.genesis)
        result = self.reopen().recover_committed_operation(
            operation_id=OP2, intent_sha256=INTENT2,
            trusted_prior_head=tuple(first["head"]))
        self.assertEqual(result["balance"], 0)

    def test_bad_type_prior_head_recovery_denied(self):
        self.committed()
        for head in (None, [0, ZERO], (True, ZERO), (0, "no")):
            with self.subTest(head=head):
                with self.assertRaises(UnsafeSourceState):
                    self.reopen().recover_committed_operation(
                        operation_id=OP1, intent_sha256=INTENT1,
                        trusted_prior_head=head)

    def test_after_local_commit_recover_cannot_debit_twice(self):
        first = self.committed()
        for _ in range(3):
            recovery = self.reopen().recover_committed_operation(
                operation_id=OP1, intent_sha256=INTENT1,
                trusted_prior_head=self.genesis)
            self.assertEqual(recovery["receipt"], first["receipt"])
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=OP1,
                           intent=INTENT1, head=tuple(first["head"]))
        self.assertEqual(self.reopen().inspect(
            trusted_head=tuple(first["head"]))["balance"], 1)

    def _signed_denial_after_stale_head(self):
        first=self.committed()
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=OP2, intent=INTENT2,
                           head=(0,ZERO))
        fence=self.reopen().inspect_stale_denial(
            operation_id=OP2,intent_sha256=INTENT2,
            trusted_prior_head=(0,ZERO),
            trusted_current_head=tuple(first["head"]))
        return first,fence

    def test_stale_loser_gets_a_signed_persistent_denial(self):
        first, result=self._signed_denial_after_stale_head()
        self.assertEqual(result["balance"],1)
        self.assertFalse(result["automatic_retry_authorized"])
        self.assertFalse(result["agent_dispatch_authorized"])
        self.assertTrue(verify_stale_source_fence(
            result["signed_stale_attempt_fence"],
            source_public_key=self.source.signer.public_key(),
            expected_source_pin=self.pin,expected_operation=OP2,
            expected_intent_sha256=INTENT2,
            expected_prior_head=(0,ZERO),
            expected_observed_head=tuple(first["head"])))
        with sqlite3.connect(self.source.dbpath) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM denied_intents").fetchone()[0],1)
            self.assertEqual(db.execute(
                "SELECT balance FROM resource").fetchone()[0],1)

    def test_stale_loser_cannot_rebase_after_winner_advances(self):
        first,_=self._signed_denial_after_stale_head()
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=OP2,intent=INTENT2,
                           head=tuple(first["head"]))
        snap=self.reopen().inspect(trusted_head=tuple(first["head"]))
        self.assertEqual(snap["balance"],1)
        self.assertEqual(snap["head"],first["head"])

    def test_signed_stale_fence_survives_newer_unrelated_debit(self):
        first,fence=self._signed_denial_after_stale_head()
        second=self.committed(
            operation_id="3"*32,
            intent=hashlib.sha256(b"distinct-second-debit").hexdigest(),
            head=tuple(first["head"]))
        self.assertEqual(second["balance"],0)
        verified=self.reopen().inspect_stale_denial(
            operation_id=OP2,intent_sha256=INTENT2,
            trusted_prior_head=(0,ZERO),
            trusted_current_head=tuple(second["head"]))
        self.assertEqual(verified["signed_stale_attempt_fence"],
                         fence["signed_stale_attempt_fence"])
        self.assertEqual(verified["balance"],0)
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=OP2, intent=INTENT2,
                           head=tuple(second["head"]))

    def test_wrong_stale_fence_root_or_head_denied(self):
        first,_=self._signed_denial_after_stale_head()
        for changed in (
            {"operation_id":"f"*32},
            {"intent_sha256":"f"*64},
            {"trusted_prior_head":(0,"f"*64)},
            {"trusted_current_head":(0,ZERO)},
        ):
            with self.subTest(changed=changed):
                kwargs=dict(
                    operation_id=OP2,intent_sha256=INTENT2,
                    trusted_prior_head=(0,ZERO),
                    trusted_current_head=tuple(first["head"]))
                kwargs.update(changed)
                with self.assertRaises(UnsafeSourceState):
                    self.reopen().inspect_stale_denial(**kwargs)

    def test_wrong_key_wrong_source_fence_signature_rejected(self):
        first,denial=self._signed_denial_after_stale_head()
        args=dict(
            source_public_key=self.source.signer.public_key(),
            expected_source_pin=self.pin,expected_operation=OP2,
            expected_intent_sha256=INTENT2,
            expected_prior_head=(0,ZERO),
            expected_observed_head=tuple(first["head"]))
        signed=denial["signed_stale_attempt_fence"]
        for changed in (
            {"source_public_key":Ed25519PrivateKey.generate().public_key()},
            {"expected_source_pin":"f"*64},
            {"expected_operation":OP1},
            {"expected_prior_head":(0,"f"*64)},
        ):
            with self.subTest(changed=list(changed)):
                self.assertFalse(verify_stale_source_fence(
                    signed,**{**args,**changed}))
        altered=deepcopy(signed)
        altered["payload"]["retry_authorized"]=True
        self.assertFalse(verify_stale_source_fence(altered,**args))
        altered=deepcopy(signed)
        altered["signature"]="AAAA"+altered["signature"][4:]
        self.assertFalse(verify_stale_source_fence(altered,**args))

    def test_tampered_persisted_fence_is_not_accepted_on_reopen(self):
        first,_=self._signed_denial_after_stale_head()
        with sqlite3.connect(self.source.dbpath) as db:
            db.execute("UPDATE denied_intents SET signed_fence_json='{}'")
        with self.assertRaises(UnsafeSourceState):
            self.reopen().inspect(trusted_head=tuple(first["head"]))
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id="3"*32,intent=INTENT2,
                           head=tuple(first["head"]))

    def test_unknown_head_does_not_create_a_signed_denial(self):
        first=self.committed()
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=OP2,intent=INTENT2,
                           head=(0,"f"*64))
        with sqlite3.connect(self.source.dbpath) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM denied_intents").fetchone()[0],0)
        with self.assertRaises(UnsafeSourceState):
            self.reopen().inspect_stale_denial(
                operation_id=OP2,intent_sha256=INTENT2,
                trusted_prior_head=(0,"f"*64),
                trusted_current_head=tuple(first["head"]))

    def test_same_committed_operation_does_not_generate_stale_denial(self):
        first=self.committed()
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=OP1,intent=INTENT2,
                           head=(0,ZERO))
        with sqlite3.connect(self.source.dbpath) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM denied_intents").fetchone()[0],0)

    def test_local_denial_image_rollback_still_possible_with_old_head(self):
        first=self.committed()
        snapshot=self.root/"old-before-fence.sqlite3"
        shutil.copy2(self.source.dbpath,snapshot)
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=OP2,intent=INTENT2,head=(0,ZERO))
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=OP2,intent=INTENT2,
                           head=tuple(first["head"]))
        shutil.copy2(snapshot,self.source.dbpath)
        with self.assertRaises(UnsafeSourceState):
            self.reopen().inspect_stale_denial(
                operation_id=OP2,intent_sha256=INTENT2,
                trusted_prior_head=(0,ZERO),
                trusted_current_head=tuple(first["head"]))
        # Required NO-GO counterexample: local rollback + no independent
        # retained signed fence allows the previously denied operation.
        committed=self.committed(operation_id=OP2,intent=INTENT2,
                                 head=tuple(first["head"]))
        self.assertEqual(committed["balance"],0)

    def test_parallel_distinct_op_race_persists_one_signed_denial(self):
        barrier=threading.Barrier(2)
        responses=[]
        guard=threading.Lock()
        def send(op,intent):
            barrier.wait(timeout=8)
            try:
                result=self.committed(operation_id=op,intent=intent)
                ok=True
            except UnsafeSourceState:
                result=None
                ok=False
            with guard: responses.append((op,intent,ok,result))
        ths=[threading.Thread(target=send,args=x) for x in (
            (OP1,INTENT1),(OP2,INTENT2))]
        for t in ths:t.start()
        for t in ths:t.join(timeout=8)
        self.assertEqual(len(responses),2)
        winning=[r for r in responses if r[2]]
        losing=[r for r in responses if not r[2]]
        self.assertEqual(len(winning),1)
        self.assertEqual(len(losing),1)
        head=tuple(winning[0][3]["head"])
        op,intent,_,_=losing[0]
        denial=self.reopen().inspect_stale_denial(
            operation_id=op,intent_sha256=intent,
            trusted_prior_head=(0,ZERO),trusted_current_head=head)
        self.assertFalse(denial["automatic_retry_authorized"])
        with self.assertRaises(UnsafeSourceState):
            self.committed(operation_id=op,intent=intent,head=head)
        self.assertEqual(self.reopen().inspect(trusted_head=head)["balance"],1)

    def test_cross_signed_worker_intent_matches_resource_operation(self):
        worker = Ed25519PrivateKey.generate()
        observer = Ed25519PrivateKey.generate()
        with tempfile.TemporaryDirectory(dir=self.root) as d:
            path = Path(d) / "worker.sqlite3"
            secret = hashlib.sha256(b"worker synthetic secret").digest()
            gate = AtomicSyntheticLeaseOutbox(path, secret, now=lambda: 1000,
                                               create=True)
            token = gate.mint(
                uid=os.getuid(), owner="owner", session="session",
                worktree_sha256=hashlib.sha256(b"test worktree").hexdigest(),
                model_pin="kilo/kilo-auto/free")
            before = gate.state_digest()
            result = gate.check(
                {"schema": 1, "op": "synthetic_receipt",
                 "token": token, "seq": 1}, peer_uid=os.getuid(),
                operation_id=OP1, expected_owner="owner",
                expected_session="session",
                expected_worktree_sha256=hashlib.sha256(b"test worktree").hexdigest(),
                expected_model_pin="kilo/kilo-auto/free")
            self.assertEqual(result["status"], "synthetic_receipt_only")
            after = gate.state_digest()
            head = (result["atomic_outbox_record"],
                    result["atomic_outbox_sha256"])
            pub = worker_publish(
                signer=worker, worker_id="worker", observer_id="observer",
                observer_fingerprint=public_fingerprint(observer.public_key()),
                exported_head=gate.export_head_event(trusted_anchor=head))
            event = pub["payload"]["outbox_event"]
            source = self.committed(
                operation_id=OP1, intent=event["request_sha256"])
            args = dict(
                worker_publication=pub, worker_public_key=worker.public_key(),
                trusted_worker_pin=public_fingerprint(worker.public_key()),
                trusted_observer_pin=public_fingerprint(observer.public_key()),
                expected_worker_id="worker", expected_observer_id="observer",
                expected_operation_id=OP1,
                expected_worker_outbox_head=head,
                expected_worker_before_state=before,
                expected_worker_after_state=after,
                source_receipt=source["receipt"],
                source_public_key=self.source.signer.public_key(),
                trusted_source_pin=self.pin,
                expected_source_head=tuple(source["head"]),
                expected_source_before_state=_state_digest(2, 0, ZERO),
                expected_source_after_state=_state_digest(1, 1, ZERO),
            )
            self.assertTrue(verify_source_corresponds_to_signed_worker_intent(**args))
            tampered = deepcopy(pub)
            tampered["payload"]["outbox_event"]["operation_id"] = OP2
            self.assertFalse(verify_source_corresponds_to_signed_worker_intent(
                **{**args, "worker_publication": tampered}))
            self.assertFalse(verify_source_corresponds_to_signed_worker_intent(
                **{**args, "expected_operation_id": OP2}))
            self.assertFalse(verify_source_corresponds_to_signed_worker_intent(
                **{**args, "trusted_source_pin": "f"*64}))
            # Different operation ID/digest is never a match.
            self.assertFalse(verify_source_corresponds_to_signed_worker_intent(
                **{**args, "source_receipt": {
                    **source["receipt"], "signature": "forged"}}))


if __name__ == "__main__":
    unittest.main()
