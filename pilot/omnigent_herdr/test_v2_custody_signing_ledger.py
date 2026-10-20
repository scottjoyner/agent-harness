"""Local durable signing sequence; negative controls stay explicitly NO-GO."""
from __future__ import annotations

import base64
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from observer_checkpoint import ZERO, public_fingerprint
from test_v2_offhost_marker_witness import OffhostMarkerTests
from v2_witness_countersign import countersign
from v2_custody_signer_fixture import _execute, UnsafeResearchCustodian
from v2_custody_signing_ledger import SyntheticV2CustodyLedger


def pub64(pub):
    return base64.b64encode(pub.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw)).decode("ascii")


class DurableCustodyLedgerTests(unittest.TestCase):
    def setUp(self):
        inherited = OffhostMarkerTests(methodName="test_genesis_is_signed_read_only_and_no_dispatch")
        inherited.setUp()
        self.addCleanup(inherited.doCleanups)
        self.fx = inherited
        scratch = tempfile.TemporaryDirectory(prefix="og-v2-custody-db-test-")
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name)
        (self.home / ".cache").mkdir(mode=0o700)
        self.root = self.home / ".cache" / "og-v2-custody-K3p8qZ"
        self.root.mkdir(mode=0o700)
        root_patch = patch.object(Path, "home", return_value=self.home)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.boot = _execute(self.root, command="bootstrap", request={})
        self.dbpath = self.root / "custody-checkpoints.sqlite3"
        self.assertEqual(self.dbpath.stat().st_mode & 0o777, 0o600)
        self.i1, self.w1, _ = self.fx.stage1()
        self.witness_signer = Ed25519PrivateKey.generate()
        self.r1 = countersign(
            self.fx.witness, signer=self.witness_signer,
            witness_head=self.w1, observer_head=self.i1)
        self.req1 = self.request(
            self.r1, self.w1, self.i1, generation=1,
            previous=ZERO)
        self.signer = None

    def request(self, receipt, witness_head, observer_head, *,
                generation, previous):
        return {
            "witness_receipt": receipt,
            "witness_public_base64": pub64(self.witness_signer.public_key()),
            "witness_pin": public_fingerprint(self.witness_signer.public_key()),
            "observer_public_base64": pub64(self.fx.observer.public_key()),
            "worker_fingerprint": public_fingerprint(self.fx.worker.public_key()),
            "observer_id": "observer",
            "worker_id": "worker",
            "witness_head": list(witness_head),
            "observer_head": list(observer_head),
            "scope": "herdr-omnigent.synthetic.v2",
            "generation": generation,
            "previous_checkpoint_sha256": previous,
            "issued_at": 1700000000,
            "expires_at": 1700000600,
        }

    def sign(self, req):
        return _execute(self.root, command="sign", request=req)

    def readback(self, req):
        return _execute(self.root, command="readback", request={
            "generation": req["generation"],
            "witness_head": req["witness_head"],
            "observer_head": req["observer_head"],
            "scope": req["scope"],
        })

    def second(self, first):
        i2, w2, _, _ = self.fx._finish_ack(self.i1, self.w1)
        r2 = countersign(
            self.fx.witness, signer=self.witness_signer,
            witness_head=w2, observer_head=i2)
        req2 = self.request(
            r2, w2, i2, generation=2,
            previous=first["checkpoint_sha256"])
        return req2

    def test_bootstrap_creates_empty_separate_schema_and_no_permissions(self):
        with sqlite3.connect(self.dbpath) as db:
            appid = db.execute("PRAGMA application_id").fetchone()[0]
            self.assertEqual(appid, 0x4F47434C)
            self.assertEqual(db.execute("SELECT count(*) FROM roots").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM history").fetchone()[0], 0)
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)

    def test_first_signed_record_survives_reopened_one_shot(self):
        signed = self.sign(self.req1)
        recovered = self.readback(self.req1)
        self.assertEqual(signed["checkpoint"], recovered["checkpoint"])
        self.assertEqual(signed["checkpoint_sha256"], recovered["checkpoint_sha256"])
        self.assertFalse(recovered["agent_dispatch_authorized"])

    def test_duplicate_signature_refused_use_readback(self):
        original = self.sign(self.req1)
        for _ in range(3):
            with self.assertRaises(UnsafeResearchCustodian):
                self.sign(self.req1)
        self.assertEqual(self.readback(self.req1)["checkpoint"], original["checkpoint"])

    def test_second_generation_chained_and_strictly_advanced(self):
        first = self.sign(self.req1)
        req2 = self.second(first)
        second = self.sign(req2)
        self.assertEqual(second["ledger_generation"], 2)
        self.assertEqual(second["checkpoint"]["payload"]["previous_checkpoint_sha256"],
                         first["checkpoint_sha256"])
        self.assertEqual(self.readback(req2)["checkpoint_sha256"],
                         second["checkpoint_sha256"])
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)

    def test_stale_previous_digest_rejected(self):
        first = self.sign(self.req1)
        req2 = self.second(first)
        req2["previous_checkpoint_sha256"] = "f"*64
        with self.assertRaises(UnsafeResearchCustodian):
            self.sign(req2)

    def test_future_generation_skip_denied(self):
        first = self.sign(self.req1)
        req2 = self.second(first)
        req2["generation"] = 3
        with self.assertRaises(UnsafeResearchCustodian):
            self.sign(req2)

    def test_repeated_or_regressed_witness_record_denied(self):
        first = self.sign(self.req1)
        req2 = self.request(
            self.r1, self.w1, self.i1, generation=2,
            previous=first["checkpoint_sha256"])
        with self.assertRaises(UnsafeResearchCustodian):
            self.sign(req2)

    def test_changed_observer_public_key_denied(self):
        first = self.sign(self.req1)
        req2 = self.second(first)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        req2["observer_public_base64"] = pub64(
            Ed25519PrivateKey.generate().public_key())
        with self.assertRaises(UnsafeResearchCustodian):
            self.sign(req2)

    def test_changed_witness_public_key_denied(self):
        first = self.sign(self.req1)
        req2 = self.second(first)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        req2["witness_public_base64"] = pub64(
            Ed25519PrivateKey.generate().public_key())
        with self.assertRaises(UnsafeResearchCustodian):
            self.sign(req2)

    def test_changed_scope_and_identity_denied(self):
        first = self.sign(self.req1)
        correct = self.second(first)
        for key, value in (("scope", "malicious"),
                           ("observer_id", "changed"),
                           ("worker_fingerprint", "f"*64)):
            with self.subTest(key=key):
                req2 = {**correct, key: value}
                with self.assertRaises(UnsafeResearchCustodian):
                    self.sign(req2)

    def test_bad_readback_identity_and_scope_denied(self):
        self.sign(self.req1)
        for key, value in (
            ("scope", "other"),
            ("generation", 2),
            ("witness_head", [3, "f"*64]),
            ("observer_head", [2, "f"*64]),
        ):
            with self.subTest(key=key):
                q = {**self.req1, key: value}
                with self.assertRaises(UnsafeResearchCustodian):
                    self.readback(q)

    def test_concurrent_first_generation_only_one_signs(self):
        results = []
        barrier = threading.Barrier(5)
        lock = threading.Lock()
        def one():
            barrier.wait(timeout=8)
            try:
                self.sign(self.req1)
                value = "signed"
            except (UnsafeResearchCustodian, sqlite3.Error):
                value = "denied"
            with lock:
                results.append(value)
        threads = [threading.Thread(target=one) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=8)
        self.assertEqual(results.count("signed"), 1)
        self.assertEqual(results.count("denied"), 4)

    def test_corrupted_history_fails_closed_on_reopen(self):
        self.sign(self.req1)
        with sqlite3.connect(self.dbpath) as db:
            db.execute("UPDATE history SET checkpoint='{}' WHERE generation=1")
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)
        with self.assertRaises(UnsafeResearchCustodian):
            self.sign(self.req1)

    def test_corrupted_saved_witness_receipt_fails_closed(self):
        self.sign(self.req1)
        with sqlite3.connect(self.dbpath) as db:
            db.execute("UPDATE history SET request='{}' WHERE generation=1")
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)

    def test_tampered_root_key_denied(self):
        self.sign(self.req1)
        with sqlite3.connect(self.dbpath) as db:
            db.execute("UPDATE roots SET custodian_pin=? WHERE id=1",
                       ("f"*64,))
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)

    def test_missing_ledger_denied_and_never_recreated(self):
        self.sign(self.req1)
        self.dbpath.unlink()
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)
        with self.assertRaises(UnsafeResearchCustodian):
            self.sign(self.req1)

    def test_unsafe_symlink_or_hardlink_denied(self):
        self.sign(self.req1)
        os.link(self.dbpath, self.root / "other")
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)
        (self.root / "other").unlink()
        self.dbpath.rename(self.root / "original")
        self.dbpath.symlink_to(self.root / "original")
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)

    def test_unsafe_world_readable_inode_denied(self):
        self.sign(self.req1)
        self.dbpath.chmod(0o644)
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)

    def test_revoke_is_terminal_durable_across_restart(self):
        first = self.sign(self.req1)
        status = _execute(self.root, command="revoke", request={
            "generation": 1,
            "checkpoint_sha256": first["checkpoint_sha256"]})
        self.assertTrue(status["locally_revoked"])
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)
        with self.assertRaises(UnsafeResearchCustodian):
            self.sign(self.req1)
        with self.assertRaises(UnsafeResearchCustodian):
            _execute(self.root, command="revoke", request={
                "generation": 1,
                "checkpoint_sha256": first["checkpoint_sha256"]})

    def test_revocation_with_wrong_head_denied(self):
        first = self.sign(self.req1)
        with self.assertRaises(UnsafeResearchCustodian):
            _execute(self.root, command="revoke", request={
                "generation": 1, "checkpoint_sha256": "f"*64})
        self.assertEqual(self.readback(self.req1)["checkpoint_sha256"],
                         first["checkpoint_sha256"])

    def test_old_ledger_detected_only_if_new_head_survives_elsewhere(self):
        first = self.sign(self.req1)
        old = self.root / "oldvalid.sqlite3"
        shutil.copy2(self.dbpath, old)
        second_req = self.second(first)
        second = self.sign(second_req)
        shutil.copy2(old, self.dbpath)
        # The old state still looks internally valid! An external
        # caller retaining generation 2/head 2 denies it; an attacker
        # restoring the old expected head can read back generation 1.
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(second_req)
        self.assertEqual(self.readback(self.req1)["checkpoint_sha256"],
                         first["checkpoint_sha256"])
        self.assertNotEqual(first["checkpoint_sha256"], second["checkpoint_sha256"])

    def test_ledger_revocation_can_be_rolled_back_with_same_admin(self):
        first = self.sign(self.req1)
        old = self.root / "valid-before-revocation.sqlite3"
        shutil.copy2(self.dbpath, old)
        _execute(self.root, command="revoke", request={
            "generation": 1,
            "checkpoint_sha256": first["checkpoint_sha256"]})
        shutil.copy2(old, self.dbpath)
        # A real independent external revocation authority is mandatory.
        self.assertEqual(self.readback(self.req1)["checkpoint_sha256"],
                         first["checkpoint_sha256"])

    def test_second_checkpoint_write_error_rolls_back(self):
        from v2_custody_signing_ledger import _canonical
        from unittest.mock import patch
        from v2_custody_signing_ledger import SyntheticV2CustodyLedger
        original = _canonical
        calls = []
        def fail(value):
            calls.append(value)
            if len(calls) == 2:
                raise UnsafeResearchCustodian("injected before SQL history INSERT")
            return original(value)
        with patch("v2_custody_signing_ledger._canonical", side_effect=fail):
            with self.assertRaises(UnsafeResearchCustodian):
                self.sign(self.req1)
        with self.assertRaises(UnsafeResearchCustodian):
            self.readback(self.req1)
        self.assertEqual(self.sign(self.req1)["ledger_generation"], 1)


if __name__ == "__main__":
    unittest.main()
