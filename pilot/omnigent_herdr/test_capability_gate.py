import hashlib
import threading
import unittest
from capability_gate import SyntheticLeaseGate

SECRET = b"fixture-only-broker-signing-key-not-production-0001"
WORKTREE = hashlib.sha256(b"synthetic seed").hexdigest()


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.current = 1000
        self.gate = SyntheticLeaseGate(SECRET, now=lambda: self.current)
        self.scope = dict(uid=1000, owner="owner-a", session="session-a",
                          worktree_sha256=WORKTREE,
                          model_pin="kilo/kilo-auto/free")
        self.token = self.gate.mint(**self.scope)

    def call(self, token=None, seq=1, op="synthetic_receipt", **changes):
        scope = dict(peer_uid=1000, expected_owner="owner-a",
                     expected_session="session-a",
                     expected_worktree_sha256=WORKTREE,
                     expected_model_pin="kilo/kilo-auto/free")
        scope.update(changes)
        return self.gate.check(
            dict(schema=1, op=op, token=self.token if token is None else token,
                 seq=seq), **scope)

    def denied(self, result):
        self.assertEqual(result["status"], "denied")
        self.assertFalse(result["dispatch_authorized"])
        self.assertEqual(result["provider_requests_forwarded"], 0)

    def test_two_bounded_receipts_and_quota(self):
        for seq, number in [(1, 1), (2, 2)]:
            result = self.call(seq=seq)
            self.assertEqual(result["receipt_number"], number)
            self.assertFalse(result["dispatch_authorized"])
        self.denied(self.call(seq=3))

    def test_repeated_sequence_fails(self):
        self.call()
        self.denied(self.call())

    def test_highwater_rejects_lower_sequence(self):
        self.call(seq=10)
        self.denied(self.call(seq=9))

    def test_forged_token_fails(self):
        body, sig = self.token.split(".")
        tampered = body + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
        self.denied(self.call(token=tampered))

    def test_owner_isolation_same_uid(self):
        self.denied(self.call(expected_owner="owner-b"))
        self.assertEqual(self.call()["status"], "synthetic_receipt_only")

    def test_session_isolation(self):
        self.denied(self.call(expected_session="session-b"))

    def test_worktree_binding(self):
        self.denied(self.call(expected_worktree_sha256="f" * 64))

    def test_model_binding(self):
        self.denied(self.call(expected_model_pin="paid/model"))

    def test_uid_binding(self):
        self.denied(self.call(peer_uid=1001))

    def test_expiration(self):
        self.current = 1020
        self.denied(self.call())

    def test_future_issued_token_denied(self):
        self.current = 999
        self.denied(self.call())

    def test_clock_rollback_between_receipts_denied(self):
        self.call(seq=1)
        self.current = 999
        self.denied(self.call(seq=2))

    def test_revocation(self):
        self.gate.revoke(self.token)
        self.denied(self.call())

    def test_epoch_rotation(self):
        self.gate.rotate_epoch()
        self.denied(self.call())

    def test_provider_call_and_shell_denied(self):
        self.denied(self.call(op="dispatch"))
        self.denied(self.call(op="shell"))

    def test_lease_bad_scope_denied(self):
        for mods in ({"uid": True}, {"owner": "with space"},
                     {"worktree_sha256": "/tmp/repo"},
                     {"ttl_seconds": 61}, {"max_receipts": 3}):
            args = {**self.scope, **mods}
            with self.assertRaises(ValueError):
                self.gate.mint(**args)

    def test_missing_or_extra_request_fields_denied(self):
        args = dict(peer_uid=1000, expected_owner="owner-a",
                    expected_session="session-a", expected_worktree_sha256=WORKTREE,
                    expected_model_pin="kilo/kilo-auto/free")
        for req in ({}, [], {"schema": 1, "op": "synthetic_receipt",
                             "token": self.token, "seq": 1, "shell": "pwd"},
                    {"schema": True, "op": "synthetic_receipt",
                     "token": self.token, "seq": 1}):
            self.denied(self.gate.check(req, **args))

    def test_concurrent_replay_only_one_receipt(self):
        results = []
        lock = threading.Lock()
        def worker():
            data = self.call(seq=1)
            with lock:
                results.append(data["status"])
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for th in threads: th.start()
        for th in threads: th.join()
        self.assertEqual(results.count("synthetic_receipt_only"), 1)
        self.assertEqual(results.count("denied"), 9)

    def test_bad_secret(self):
        with self.assertRaises(ValueError):
            SyntheticLeaseGate(b"short")

    def test_noncanonical_signature_encoding_denied(self):
        from capability_gate import _decode, _encode
        body, sig = self.token.split(".")
        # sha256 digest is 32 bytes, giving a 43-char base64url string with
        # two unused low bits in its final character. Toggle those bits.
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        last = alphabet.index(sig[-1])
        changed = alphabet[(last & ~3) | ((last + 1) & 3)]
        alternate = body + "." + sig[:-1] + changed
        self.denied(self.call(token=alternate))
        self.assertNotEqual(sig[-1], changed)

    def test_bad_token_shape(self):
        self.denied(self.call(token="x" * 3000))
        self.denied(self.call(token="not.a.valid.token"))


if __name__ == "__main__":
    unittest.main()
