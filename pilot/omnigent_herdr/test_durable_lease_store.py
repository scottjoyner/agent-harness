"""Research-only restart and failure semantics for SQLite synthetic lease state."""
import hashlib
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from durable_lease_store import DurableSyntheticLeaseGate, UnsafeLeaseState

KEY = b"research-test-only-hmac-key-20261019-01234567890"
OTHER_KEY = b"incorrect-research-test-key-20261019-01234567"
WORKTREE = hashlib.sha256(b"not-user-data").hexdigest()
SCOPE = {
    "uid": 1000, "owner": "fixture-owner", "session": "sandbox-1",
    "worktree_sha256": WORKTREE, "model_pin": "kilo/kilo-auto/free",
}
VERIFY = {
    "peer_uid": 1000, "expected_owner": "fixture-owner",
    "expected_session": "sandbox-1",
    "expected_worktree_sha256": WORKTREE,
    "expected_model_pin": "kilo/kilo-auto/free",
}


class DurableResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="og-test-durable-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.db = self.root / "state.sqlite3"
        self.clock = [1000]
        self.gate = self.open(create=True)

    def open(self, **opts):
        return DurableSyntheticLeaseGate(
            self.db, opts.get("secret", KEY),
            now=lambda: self.clock[0], create=opts.get("create", False))

    def token(self):
        return self.gate.mint(**SCOPE)

    def call(self, gate, token, seq=1, **changes):
        return gate.check({
            "schema": 1, "op": "synthetic_receipt",
            "token": token, "seq": seq,
        }, **{**VERIFY, **changes})

    def denied(self, value):
        self.assertEqual(value["status"], "denied")
        self.assertIs(value["dispatch_authorized"], False)
        self.assertEqual(value["provider_requests_forwarded"], 0)

    def test_db_mode_and_private_directory(self):
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_replay_remains_rejected_after_restart(self):
        token = self.token()
        self.assertEqual(self.call(self.gate, token)["receipt_number"], 1)
        after = self.open()
        self.denied(self.call(after, token))
        self.assertEqual(self.call(after, token, seq=2)["receipt_number"], 2)
        after_again = self.open()
        self.denied(self.call(after_again, token, seq=3))

    def test_restarted_broker_retains_revocation(self):
        token = self.token()
        self.assertTrue(self.gate.revoke(token))
        self.denied(self.call(self.open(), token))

    def test_restart_preserves_epoch(self):
        token = self.token()
        self.assertEqual(self.gate.rotate_epoch(), 2)
        restarted = self.open()
        self.denied(self.call(restarted, token))
        next_token = restarted.mint(**SCOPE)
        self.assertEqual(self.call(restarted, next_token)["receipt_number"], 1)

    def test_persistent_wallclock_roll_back_denied(self):
        token = self.token()
        self.assertEqual(self.call(self.gate, token)["receipt_number"], 1)
        self.clock[0] = 999
        after = self.open()
        self.denied(self.call(after, token, seq=2))
        with self.assertRaises(UnsafeLeaseState):
            after.mint(**SCOPE)

    def test_expiration_after_restart(self):
        token = self.token()
        self.clock[0] = 1021
        self.denied(self.call(self.open(), token))

    def test_wrong_signing_secret_refused_at_open(self):
        self.token()
        with self.assertRaises(UnsafeLeaseState):
            self.open(secret=OTHER_KEY)

    def test_missing_db_refuses_implicit_reinitialization(self):
        self.db.unlink()
        with self.assertRaises(UnsafeLeaseState):
            self.open()
        self.assertFalse(self.db.exists())

    def test_existing_db_cannot_be_overwritten(self):
        self.token()
        with self.assertRaises(UnsafeLeaseState):
            self.open(create=True)

    def test_truncated_db_rejected(self):
        self.token()
        self.db.write_bytes(b"corrupt")
        with self.assertRaises(UnsafeLeaseState):
            self.open()

    def test_symlink_db_refused(self):
        self.db.rename(self.root / "original.sqlite3")
        self.db.symlink_to(self.root / "original.sqlite3")
        with self.assertRaises(UnsafeLeaseState):
            self.open()

    def test_hardlinked_db_refused(self):
        alias = self.root / "alias.sqlite3"
        os.link(self.db, alias)
        with self.assertRaises(UnsafeLeaseState):
            self.open()

    def test_exposed_db_refused(self):
        os.chmod(self.db, 0o644)
        with self.assertRaises(UnsafeLeaseState):
            self.open()

    def test_exposed_parent_refused(self):
        os.chmod(self.root, 0o755)
        with self.assertRaises(UnsafeLeaseState):
            self.open()

    def test_cross_owner_and_scope_fail_closed(self):
        token = self.token()
        for change in (
            {"peer_uid": 2000}, {"expected_owner": "other"},
            {"expected_session": "other"},
            {"expected_model_pin": "paid-model"},
            {"expected_worktree_sha256": "0" * 64},
        ):
            self.denied(self.call(self.open(), token, **change))
        self.assertEqual(self.call(self.open(), token)["receipt_number"], 1)

    def test_negative_ops_do_not_mutate_quota(self):
        token = self.token()
        self.denied(self.gate.check({
            "schema": 1, "op": "dispatch", "token": token, "seq": 1,
        }, **VERIFY))
        self.assertEqual(self.call(self.open(), token)["receipt_number"], 1)

    def test_concurrent_same_sequence_across_two_brokers(self):
        token = self.token()
        a, b = self.open(), self.open()
        barrier = threading.Barrier(12)
        outputs = []
        mutex = threading.Lock()
        def once(n):
            barrier.wait()
            answer = self.call(a if n % 2 == 0 else b, token, seq=1)
            with mutex:
                outputs.append(answer["status"])
        threads = [threading.Thread(target=once, args=(i,)) for i in range(12)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=12)
        self.assertEqual(len(outputs), 12)
        self.assertEqual(outputs.count("synthetic_receipt_only"), 1)
        self.assertEqual(outputs.count("denied"), 11)

    def test_restart_checkpoint_digest_changes_on_accepted_receipt(self):
        before = self.gate.state_digest()
        token = self.token()
        minted = self.open().state_digest()
        self.assertNotEqual(before, minted)
        self.call(self.open(), token)
        after = self.open().state_digest()
        self.assertNotEqual(minted, after)
        self.assertEqual(after, self.open().state_digest())

    def test_durable_db_can_be_rolled_back_without_independent_witness(self):
        """Intentionally demonstrates the remaining production-blocking attack."""
        token = self.token()
        old_image = self.root / "snapshot"
        shutil.copy2(self.db, old_image)
        self.assertEqual(self.call(self.gate, token)["receipt_number"], 1)
        shutil.copy2(old_image, self.db)
        # Cooperative filesystem state cannot detect a valid old image.
        # A separate, trusted high-water/checkpoint is required.
        self.assertEqual(self.call(self.open(), token)["receipt_number"], 1)

    def test_revoke_old_key_does_not_mint_dispatch(self):
        token = self.token()
        self.assertTrue(self.gate.revoke(token))
        self.assertFalse(self.gate.revoke("invalid"))
        self.denied(self.call(self.open(), token))


if __name__ == "__main__":
    unittest.main()
