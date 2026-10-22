"""Lost response/partition/rollback effects must not authorize a retry."""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from observer_checkpoint import ZERO, public_fingerprint
from v2_outbox_publication import worker_publish
from v2_source_transaction_fixture import SyntheticResourceSource
from v2_cross_host_uncertainty import classify_source_recovery

OP = "1"*32
WORKTREE = hashlib.sha256(b"uncertainty-test").hexdigest()


class FailClosedSourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="og-v2-uncertain-test-")
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name)
        (self.home / ".cache").mkdir(mode=0o700)
        self.root = self.home / ".cache" / "og-v2-source-AB71pq"
        self.root.mkdir(mode=0o700)
        home = patch.object(Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        self.source = SyntheticResourceSource(self.root, create=True)
        self.worker = Ed25519PrivateKey.generate()
        self.observer = Ed25519PrivateKey.generate()
        # Worker store must also be private.
        wroot = self.home / "worker"
        wroot.mkdir(mode=0o700)
        self.gate = AtomicSyntheticLeaseOutbox(
            wroot / "worker.sqlite3", os.urandom(32), now=lambda: 1000,
            create=True)
        token = self.gate.mint(
            uid=os.getuid(), owner="owner", session="session",
            worktree_sha256=WORKTREE, model_pin="kilo/kilo-auto/free")
        self.before = self.gate.state_digest()
        outcome = self.gate.check(
            {"schema": 1, "op": "synthetic_receipt", "token": token, "seq": 1},
            peer_uid=os.getuid(), operation_id=OP,
            expected_owner="owner", expected_session="session",
            expected_worktree_sha256=WORKTREE,
            expected_model_pin="kilo/kilo-auto/free")
        self.assertEqual(outcome["status"], "synthetic_receipt_only")
        self.after = self.gate.state_digest()
        self.worker_head = (outcome["atomic_outbox_record"],
                            outcome["atomic_outbox_sha256"])
        self.publication = worker_publish(
            signer=self.worker, worker_id="worker", observer_id="observer",
            observer_fingerprint=public_fingerprint(self.observer.public_key()),
            exported_head=self.gate.export_head_event(
                trusted_anchor=self.worker_head))
        self.intent = self.publication["payload"]["outbox_event"]["request_sha256"]
        self.params = dict(
            worker_publication=self.publication,
            worker_public_key=self.worker.public_key(),
            trusted_worker_pin=public_fingerprint(self.worker.public_key()),
            trusted_observer_pin=public_fingerprint(self.observer.public_key()),
            expected_worker_id="worker",
            expected_observer_id="observer",
            expected_operation_id=OP,
            expected_worker_outbox_head=self.worker_head,
            expected_worker_before_state=self.before,
            expected_worker_after_state=self.after,
            source_public_key=self.source.signer.public_key(),
            trusted_source_pin=self.source.pin,
            trusted_prior_source_head=(0, ZERO),
        )
        self.committed = self.source.commit(
            operation_id=OP, intent_sha256=self.intent,
            trusted_head=(0, ZERO))
        self.recovered = self.source.recover_committed_operation(
            operation_id=OP, intent_sha256=self.intent,
            trusted_prior_head=(0, ZERO))

    def classify(self, response=None, **updates):
        # Use explicit None to represent dropped source response.
        return classify_source_recovery(
            source_reply=response, **{**self.params, **updates})

    def test_signed_read_only_recovery_is_source_local_hold(self):
        decision = self.classify(self.recovered)
        self.assertEqual(decision["status"], "SOURCE_LOCAL_COMMITTED_HOLD")
        self.assertTrue(decision["source_local_commit_verified"])
        self.assertEqual(decision["decision"], "HOLD")
        self.assertFalse(decision["agent_dispatch_authorized"])
        self.assertFalse(decision["automatic_retry_authorized"])
        self.assertFalse(decision["automatic_refund_authorized"])
        self.assertFalse(decision["lease_unfence_authorized"])
        self.assertFalse(decision["worker_source_distributed_atomicity_verified"])

    def test_dropped_ssh_reply_is_uncertain_not_no_effect(self):
        self.assertEqual(self.classify()["status"], "UNCERTAIN_HOLD")
        self.assertFalse(self.classify()["automatic_retry_authorized"])

    def test_disconnected_source_during_partition_is_uncertain(self):
        self.assertEqual(self.classify(None)["status"], "UNCERTAIN_HOLD")

    def test_source_absent_is_uncertain_not_retryable(self):
        self.assertEqual(self.classify({
            "decision": "HOLD", "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0,
            "error": "resource source denied",
        })["status"], "UNCERTAIN_HOLD")

    def test_wrong_source_key_quarantines_recovered_claim(self):
        rogue = Ed25519PrivateKey.generate()
        self.assertEqual(self.classify(
            self.recovered,
            source_public_key=rogue.public_key())["status"],
            "QUARANTINED_HOLD")

    def test_wrong_prior_anchor_quarantines(self):
        self.assertEqual(self.classify(
            self.recovered,
            trusted_prior_source_head=(0, "f"*64))["status"],
            "QUARANTINED_HOLD")

    def test_wrong_operation_quarantines(self):
        self.assertEqual(self.classify(
            self.recovered,
            expected_operation_id="2"*32)["status"],
            "QUARANTINED_HOLD")

    def test_wrong_worker_publication_quarantines(self):
        bad = deepcopy(self.publication)
        bad["payload"]["outbox_event"]["operation_id"] = "2"*32
        self.assertEqual(self.classify(
            self.recovered,
            worker_publication=bad)["status"], "QUARANTINED_HOLD")

    def test_wrong_source_intent_receipt_quarantines(self):
        bad = deepcopy(self.recovered)
        bad["receipt"]["payload"]["intent_sha256"] = "f"*64
        self.assertEqual(self.classify(bad)["status"], "QUARANTINED_HOLD")

    def test_tampered_signature_quarantines(self):
        bad = deepcopy(self.recovered)
        bad["receipt"]["signature"] = "AAAA" + bad["receipt"]["signature"][4:]
        self.assertEqual(self.classify(bad)["status"], "QUARANTINED_HOLD")

    def test_wrong_source_reply_shape_quarantines(self):
        for bad in ({"status": "success"}, {}, [],
                    {"error": "mystery"}, {"agent_dispatch_authorized": True}):
            with self.subTest(payload=bad):
                self.assertEqual(self.classify(bad)["status"],
                                 "QUARANTINED_HOLD")

    def test_forged_dispatch_or_model_enable_quarantines(self):
        for name in ("agent_dispatch_authorized", "model_egress_authorized",
                     "source_is_real_provider",
                     "independently_administered_source_verified"):
            bad = {**self.recovered, name: True}
            with self.subTest(name=name):
                self.assertEqual(self.classify(bad)["status"],
                                 "QUARANTINED_HOLD")

    def test_fake_source_balance_quarantines(self):
        bad = {**self.recovered, "balance": 2}
        self.assertEqual(self.classify(bad)["status"], "QUARANTINED_HOLD")

    def test_stale_source_head_quarantines(self):
        bad = {**self.recovered, "head": [0, ZERO]}
        self.assertEqual(self.classify(bad)["status"], "QUARANTINED_HOLD")

    def test_unsafe_booleans_as_integer_rejected(self):
        for field in ("schema", "provider_requests_forwarded"):
            bad = {**self.recovered, field: True}
            with self.subTest(field=field):
                self.assertEqual(self.classify(bad)["status"], "QUARANTINED_HOLD")

    def test_source_balance_never_debits_on_recovery(self):
        for _ in range(3):
            self.assertTrue(self.classify(self.source.recover_committed_operation(
                operation_id=OP, intent_sha256=self.intent,
                trusted_prior_head=(0, ZERO)))["source_local_commit_verified"])
        self.assertEqual(self.source.inspect(
            trusted_head=tuple(self.committed["head"]))["balance"], 1)

    def test_readback_is_not_a_second_worker_local_debit(self):
        self.classify(self.recovered)
        self.assertEqual(
            self.gate.inspect(trusted_anchor=self.worker_head)["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
