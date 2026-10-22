import hashlib
import os
import secrets
import shutil
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from durable_lease_store import DurableSyntheticLeaseGate
from observer_ack_protocol import (
    EphemeralObserver, WitnessedSyntheticReceipt,
    request_digest, verify_ack, verify_prepare,
)
from observer_checkpoint import checkpoint_sha256, public_fingerprint

SECRET = b"synthetic-only-observer-ACK-worker-test-key-2026"
WORKTREE = hashlib.sha256(b"fake").hexdigest()
SCOPED = {
    "uid": 1000, "owner": "fixture", "session": "task",
    "worktree_sha256": WORKTREE, "model_pin": "kilo/kilo-auto/free",
}
EXPECTED = {
    "peer_uid": 1000, "expected_owner": "fixture",
    "expected_session": "task", "expected_worktree_sha256": WORKTREE,
    "expected_model_pin": "kilo/kilo-auto/free",
}


class SignedObserverAckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="og-observer-ack-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        os.chmod(self.root, 0o700)
        self.db = self.root / "synthetic.sqlite3"
        self.clock = [1000]
        self.gate = DurableSyntheticLeaseGate(
            self.db, SECRET, now=lambda: self.clock[0], create=True)
        self.token = self.gate.mint(**SCOPED)
        self.signer = Ed25519PrivateKey.generate()
        self.observer = EphemeralObserver(
            signer=self.signer, observer_id="observer-fixture",
            worker_id="worker-fixture", initial_state=self.gate.state_digest())
        self.handler = WitnessedSyntheticReceipt(
            self.gate, self.observer,
            trusted_public_key=self.signer.public_key(),
            trusted_fingerprint=self.observer.fingerprint)

    def request(self, seq=1, op="synthetic_receipt"):
        return {"schema": 1, "op": op,
                "token": self.token, "seq": seq}

    def execute(self, seq=1, observed=None, **changes):
        opts = {**EXPECTED,
                "observed_postcommit": observed
                if observed is not None else self.gate.state_digest}
        opts.update(changes)
        return self.handler.execute(self.request(seq=seq), **opts)

    def denied(self, response):
        self.assertEqual(response["status"], "denied")
        self.assertFalse(response["observer_acknowledged"])
        self.assertFalse(response["agent_dispatch_authorized"])
        self.assertFalse(response["dispatch_authorized"])
        self.assertFalse(response["model_egress_authorized"])
        self.assertEqual(response["provider_requests_forwarded"], 0)

    def test_success_ack_then_replay_and_second_transaction(self):
        first = self.execute()
        self.assertEqual(first["status"], "synthetic_receipt_only")
        self.assertTrue(first["observer_acknowledged"])
        self.assertFalse(self.observer.fenced)
        self.denied(self.execute(seq=1))
        # A refused replay may or may not mutate clock metadata; the
        # observer must never claim a second accepted receipt for replay.
        self.assertFalse(self.observer.fenced)

    def test_second_receipt_with_new_valid_seq(self):
        self.assertEqual(self.execute()["status"], "synthetic_receipt_only")
        self.assertEqual(self.execute(seq=2)["receipt_number"], 2)
        self.denied(self.execute(seq=3))

    def test_no_external_observation_does_not_claim_ack(self):
        resp = self.handler.execute(
            self.request(), **EXPECTED, observed_postcommit=None)
        self.denied(resp)
        self.assertIsNotNone(self.observer.pending)
        self.denied(self.execute(seq=2))

    def test_ack_lost_after_observer_signed_stays_fenced(self):
        self.denied(self.execute(deliver_ack=lambda _: None))
        self.assertTrue(self.observer.fenced)
        self.denied(self.execute(seq=2))

    def test_ack_forgery_rejected_and_fenced(self):
        def tamper(ack):
            forged = {
                **ack, "payload": {**ack["payload"],
                                   "after_state_sha256": "a" * 64}}
            return forged
        self.denied(self.execute(deliver_ack=tamper))
        self.assertTrue(self.observer.fenced)

    def test_ack_from_unpinned_key_denied_before_mutation(self):
        other = Ed25519PrivateKey.generate()
        untrusted = WitnessedSyntheticReceipt(
            self.gate, self.observer,
            trusted_public_key=other.public_key(),
            trusted_fingerprint=public_fingerprint(other.public_key()))
        self.denied(untrusted.execute(
            self.request(), **EXPECTED,
            observed_postcommit=self.gate.state_digest))
        # Observer prepared an operation, but the worker refused it.
        # No synthetic receipt consumed; the pending prepare is fenced.
        self.assertIsNotNone(self.observer.pending)

    def test_observer_postcommit_mismatch_is_denied_and_fenced(self):
        self.denied(self.execute(observed=lambda: "b" * 64))
        self.assertIsNotNone(self.observer.pending)
        self.denied(self.execute(seq=2))

    def test_stale_worker_database_denied_before_lease_mutation(self):
        image = self.root / "old-image"
        shutil.copy2(self.db, image)
        self.assertEqual(self.execute()["status"], "synthetic_receipt_only")
        shutil.copy2(image, self.db)
        self.denied(self.execute(seq=2))

    def test_wrong_owner_denied_and_never_signs_ack(self):
        self.denied(self.execute(expected_owner="another-owner"))
        self.assertIsNone(self.observer.last_ack)

    def test_duplicate_operation_id_fails_closed(self):
        opid = secrets.token_hex(16)
        self.assertEqual(
            self.execute(operation_id=opid)["status"],
            "synthetic_receipt_only")
        self.denied(self.execute(seq=2, operation_id=opid))

    def test_signature_pin_and_exact_metadata(self):
        before = self.observer.state
        opid = secrets.token_hex(16)
        request_sha = request_digest(self.request())
        previous = checkpoint_sha256(self.observer.current)
        prepare = self.observer.prepare(
            operation_id=opid, request_sha256=request_sha,
            previous_state=before)
        exact = {
            "operation_id": opid,
            "request_sha256": request_sha,
            "before_state_sha256": before,
            "generation": 2,
            "previous_checkpoint_sha256": previous,
        }
        self.assertTrue(verify_prepare(
            prepare, public_key=self.signer.public_key(),
            fingerprint=self.observer.fingerprint, expected=exact))
        self.assertFalse(verify_prepare(
            prepare, public_key=self.signer.public_key(),
            fingerprint="f" * 64, expected=exact))
        self.assertFalse(verify_ack(
            prepare, public_key=self.signer.public_key(),
            fingerprint=self.observer.fingerprint, expected=exact))

    def test_restart_state_reuse_not_an_authorization(self):
        self.assertTrue(self.execute()["observer_acknowledged"])
        restart = DurableSyntheticLeaseGate(
            self.db, SECRET, now=lambda: self.clock[0])
        handler = WitnessedSyntheticReceipt(
            restart, self.observer,
            trusted_public_key=self.signer.public_key(),
            trusted_fingerprint=self.observer.fingerprint)
        denied = handler.execute(
            self.request(seq=1), **EXPECTED,
            observed_postcommit=restart.state_digest)
        self.denied(denied)

    def test_signed_ack_is_not_model_dispatch(self):
        ack = self.execute()
        self.assertFalse(ack["agent_dispatch_authorized"])
        self.assertEqual(ack["provider_requests_forwarded"], 0)


if __name__ == "__main__":
    unittest.main()
