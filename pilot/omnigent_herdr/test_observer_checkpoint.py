import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from durable_lease_store import DurableSyntheticLeaseGate
from observer_checkpoint import (
    ZERO, checkpoint_sha256, public_fingerprint,
    read_local_state_digest, sign_fixture, verify,
)

SECRET = b"fixture-worker-key-unrelated-to-observer-123456789"
WORKTREE = hashlib.sha256(b"throwaway sample").hexdigest()
SCOPE = {"uid": 1000, "owner": "fixture-owner", "session": "fixture-session",
         "worktree_sha256": WORKTREE, "model_pin": "kilo/kilo-auto/free"}
VERIFY = {"peer_uid": 1000, "expected_owner": "fixture-owner",
          "expected_session": "fixture-session",
          "expected_worktree_sha256": WORKTREE,
          "expected_model_pin": "kilo/kilo-auto/free"}


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="observer-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.db = self.root / "research.sqlite3"
        self.gate = DurableSyntheticLeaseGate(
            self.db, SECRET, create=True, now=lambda: 1000)
        self.observer_key = Ed25519PrivateKey.generate()
        self.public = self.observer_key.public_key()
        self.fingerprint = public_fingerprint(self.public)
        self.other_signer = Ed25519PrivateKey.generate()

    def sign(self, *, generation, previous=ZERO):
        return sign_fixture(
            observer_key=self.observer_key, observer_id="observer-fixture",
            worker_id="worker-fixture",
            state_sha256=read_local_state_digest(self.db),
            generation=generation, previous_checkpoint_sha256=previous,
            created_unix=1000)

    def check(self, record, *, generation=1, **changes):
        return verify(
            state_path=self.db, envelope=record,
            observer_public_key=self.public,
            trusted_observer_fingerprint=self.fingerprint,
            expected_observer_id="observer-fixture",
            expected_worker_id="worker-fixture",
            min_generation=generation, **changes)

    def test_identical_state_digest_and_signed_checkpoint(self):
        self.assertEqual(self.gate.state_digest(), read_local_state_digest(self.db))
        checkpoint = self.sign(generation=1)
        result = self.check(checkpoint)
        self.assertTrue(result["signature_valid"])
        self.assertTrue(result["checkpoint_matches_sqlite"])
        self.assertTrue(result["checkpoint_generation_acceptable"])
        self.assertFalse(result["physical_observer_custody_verified"])
        self.assertFalse(result["continuous_publication_verified"])
        self.assertFalse(result["rollback_resistance_verified"])
        self.assertFalse(result["agent_dispatch_authorized"])
        self.assertEqual(result["decision"], "HOLD")

    def test_changed_state_rejected_by_old_checkpoint(self):
        cp = self.sign(generation=1)
        self.gate.mint(**SCOPE)
        after = self.check(cp)
        self.assertTrue(after["signature_valid"])
        self.assertFalse(after["checkpoint_matches_sqlite"])

    def test_rollback_rejected_when_latest_checkpoint_known(self):
        token = self.gate.mint(**SCOPE)
        cp1 = self.sign(generation=1)
        snapshot = self.root / "old-snapshot"
        shutil.copy2(self.db, snapshot)
        self.assertEqual(
            self.gate.check({"schema":1, "op":"synthetic_receipt",
                             "token":token, "seq":1}, **VERIFY)["receipt_number"], 1)
        cp2 = self.sign(generation=2, previous=checkpoint_sha256(cp1))
        self.assertTrue(self.check(cp2, generation=2,
                                   trusted_previous_sha256=checkpoint_sha256(cp1))[
                                        "checkpoint_matches_sqlite"])
        shutil.copy2(snapshot, self.db)
        rolled = self.check(cp2, generation=2,
                            trusted_previous_sha256=checkpoint_sha256(cp1))
        self.assertTrue(rolled["signature_valid"])
        self.assertFalse(rolled["checkpoint_matches_sqlite"])
        stale = self.check(cp1, generation=2)
        self.assertTrue(stale["signature_valid"])
        self.assertFalse(stale["checkpoint_generation_acceptable"])
        # The explicit negative control: if the caller supplies stale
        # floor=1, an old signed checkpoint + old SQLite DB still matches.
        old_floor = self.check(cp1, generation=1)
        self.assertTrue(old_floor["checkpoint_matches_sqlite"])

    def test_wrong_signer_key_fingerprint_fails(self):
        cp = self.sign(generation=1)
        result = verify(
            state_path=self.db, envelope=cp,
            observer_public_key=self.other_signer.public_key(),
            trusted_observer_fingerprint=self.fingerprint,
            expected_observer_id="observer-fixture",
            expected_worker_id="worker-fixture",
            min_generation=1)
        self.assertFalse(result["independent_trust_root_configured"])
        self.assertFalse(result["signature_valid"])

    def test_signature_edit_denied(self):
        cp = self.sign(generation=1)
        cp["signature"] = "A" + cp["signature"][1:]
        # In extremely unlikely event first byte already A, alter another.
        if self.check(cp)["signature_valid"]:
            cp["signature"] = "B" + cp["signature"][1:]
        self.assertFalse(self.check(cp)["signature_valid"])

    def test_state_edit_without_resign_denied(self):
        cp = self.sign(generation=1)
        cp["payload"]["state_sha256"] = "f" * 64
        self.assertFalse(self.check(cp)["signature_valid"])

    def test_wrong_role_denied(self):
        cp = self.sign(generation=1)
        result = verify(
            state_path=self.db, envelope=cp,
            observer_public_key=self.public,
            trusted_observer_fingerprint=self.fingerprint,
            expected_observer_id="worker-fixture",
            expected_worker_id="observer-fixture",
            min_generation=1)
        self.assertFalse(result["signature_valid"])

    def test_previous_chain_mismatch_fails(self):
        cp = self.sign(generation=2, previous="1" * 64)
        result = self.check(cp, generation=2,
                            trusted_previous_sha256="2" * 64)
        self.assertTrue(result["signature_valid"])
        self.assertFalse(result["checkpoint_generation_acceptable"])

    def test_bad_generation_type_denied(self):
        cp = self.sign(generation=1)
        cp["payload"]["generation"] = True
        self.assertFalse(self.check(cp)["signature_valid"])

    def test_missing_db_fails_closed(self):
        cp = self.sign(generation=1)
        self.db.unlink()
        result = self.check(cp)
        self.assertTrue(result["signature_valid"])
        self.assertFalse(result["checkpoint_matches_sqlite"])

    def test_symlink_db_denied(self):
        cp = self.sign(generation=1)
        name = self.root / "alias.sqlite3"
        name.symlink_to(self.db)
        result = verify(
            state_path=name, envelope=cp,
            observer_public_key=self.public,
            trusted_observer_fingerprint=self.fingerprint,
            expected_observer_id="observer-fixture",
            expected_worker_id="worker-fixture",
            min_generation=1)
        self.assertFalse(result["checkpoint_matches_sqlite"])

    def test_observer_role_cannot_equal_worker(self):
        with self.assertRaises(ValueError):
            sign_fixture(
                observer_key=self.observer_key,
                observer_id="same", worker_id="same",
                state_sha256=self.gate.state_digest(),
                generation=1, previous_checkpoint_sha256=ZERO, created_unix=1000)

    def test_genesis_cannot_claim_predecessor(self):
        with self.assertRaises(ValueError):
            self.sign(generation=1, previous="c" * 64)

    def test_no_state_mutation_from_observer_read(self):
        before = self.db.read_bytes()
        digest = read_local_state_digest(self.db)
        after = self.db.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(digest, self.gate.state_digest())


if __name__ == "__main__":
    unittest.main()
