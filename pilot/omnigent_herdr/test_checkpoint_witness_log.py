"""Negative and cooperative append-only tests for synthetic witness logs."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from checkpoint_witness_log import CheckpointWitnessLog, UnsafeWitness
from observer_checkpoint import ZERO, checkpoint_sha256, sign_fixture

FIRST = "1" * 64
SECOND = "2" * 64
THIRD = "3" * 64


class WitnessLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-witness-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.path = self.root / "observer-witness.jsonl"
        self.key = Ed25519PrivateKey.generate()
        self.log = CheckpointWitnessLog(
            self.path, signer=self.key, observer_public_key=self.key.public_key(),
            observer_id="x1-synthetic-observer", worker_id="lenovo-synthetic-worker")
        self.cp1 = self.checkpoint(1, FIRST, ZERO)
        self.anchor1 = self.log.publish(self.cp1, trusted_anchor=None, genesis=True)

    def checkpoint(self, generation, state, prev, signer=None):
        return sign_fixture(
            observer_key=signer or self.key,
            observer_id="x1-synthetic-observer", worker_id="lenovo-synthetic-worker",
            state_sha256=state, generation=generation,
            previous_checkpoint_sha256=prev, created_unix=1000+generation)

    def append_second(self):
        cp = self.checkpoint(2, SECOND, checkpoint_sha256(self.cp1))
        a2 = self.log.publish(cp, trusted_anchor=self.anchor1)
        return cp, a2

    def verifies_only_with_supplied_anchor(self, anchor):
        viewer = CheckpointWitnessLog(
            self.path, observer_public_key=self.key.public_key(),
            observer_id="x1-synthetic-observer", worker_id="lenovo-synthetic-worker")
        return viewer.inspect(trusted_anchor=anchor)

    def test_genesis_private_file_and_non_authorizing_result(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.anchor1[0], 1)
        receipt = self.verifies_only_with_supplied_anchor(self.anchor1)
        self.assertEqual(receipt["latest_state_sha256"], FIRST)
        self.assertFalse(receipt["agent_dispatch_authorized"])
        self.assertFalse(receipt["rollback_resistance_verified"])
        self.assertEqual(receipt["decision"], "HOLD")

    def test_two_monotonic_signed_checkpoints(self):
        cp2, anchor2 = self.append_second()
        receipt = self.verifies_only_with_supplied_anchor(anchor2)
        self.assertEqual(receipt["records"], 2)
        self.assertEqual(receipt["latest_generation"], 2)
        self.assertEqual(receipt["latest_checkpoint_sha256"], checkpoint_sha256(cp2))
        self.assertEqual(receipt["latest_record_sha256"], anchor2[1])

    def test_stale_writer_anchor_refuses_next_append(self):
        cp2, anchor2 = self.append_second()
        cp3 = self.checkpoint(3, THIRD, checkpoint_sha256(cp2))
        with self.assertRaises(UnsafeWitness):
            self.log.publish(cp3, trusted_anchor=self.anchor1)
        self.assertEqual(self.verifies_only_with_supplied_anchor(anchor2)["records"], 2)

    def test_replay_same_checkpoint_fails_generation(self):
        _, anchor2 = self.append_second()
        with self.assertRaises(UnsafeWitness):
            self.log.publish(self.cp1, trusted_anchor=anchor2)

    def test_valid_old_log_restore_rejected_if_anchor_is_new(self):
        old = self.root / "previous-valid-image"
        shutil.copy2(self.path, old)
        _, anchor2 = self.append_second()
        shutil.copy2(old, self.path)
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(anchor2)
        with self.assertRaises(UnsafeWitness):
            self.log.publish(
                self.checkpoint(2, SECOND, checkpoint_sha256(self.cp1)),
                trusted_anchor=anchor2)

    def test_old_log_and_old_trusted_anchor_is_negative_control(self):
        old = self.root / "old-valid-image"
        shutil.copy2(self.path, old)
        self.append_second()
        shutil.copy2(old, self.path)
        # This is the important UNSOLVED rollback attack: a malicious
        # administrator can replace BOTH witness and external anchor.
        self.assertEqual(self.verifies_only_with_supplied_anchor(self.anchor1)["records"], 1)

    def test_missing_external_anchor_refused(self):
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(None)

    def test_wrong_trusted_anchor_hash_refused(self):
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor((1, "f" * 64))

    def test_unsigned_or_wrong_signer_checkpoint_denied(self):
        other = Ed25519PrivateKey.generate()
        alien = self.checkpoint(2, SECOND, checkpoint_sha256(self.cp1), signer=other)
        with self.assertRaises(UnsafeWitness):
            self.log.publish(alien, trusted_anchor=self.anchor1)

    def test_broken_checkpoint_predecessor_refused(self):
        invalid = self.checkpoint(2, SECOND, ZERO)
        with self.assertRaises(UnsafeWitness):
            self.log.publish(invalid, trusted_anchor=self.anchor1)

    def test_truncated_line_fail_closed(self):
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(self.anchor1)

    def test_corrupt_payload_fail_closed(self):
        raw = self.path.read_bytes()
        self.path.write_bytes(raw.replace(b'"generation":1', b'"generation":3', 1))
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(self.anchor1)

    def test_noncanonical_whitespace_fails_closed(self):
        raw = self.path.read_bytes()
        self.path.write_bytes(raw.replace(b',"generation"', b', "generation"', 1))
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(self.anchor1)

    def test_duplicate_json_key_fails_closed(self):
        record = json.loads(self.path.read_text().strip())
        line = self.path.read_bytes().replace(
            b'"schema":1', b'"schema":1,"schema":1', 1)
        self.path.write_bytes(line)
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(self.anchor1)

    def test_empty_file_refused_for_inspection(self):
        self.path.write_bytes(b"")
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(self.anchor1)

    def test_verifier_uses_read_only_descriptor(self):
        from unittest.mock import patch
        import checkpoint_witness_log as module
        original = module.os.open
        observed = []

        def traced(path, flags, *args, **kwargs):
            if Path(path) == self.path:
                observed.append(flags)
            return original(path, flags, *args, **kwargs)

        with patch.object(module.os, "open", side_effect=traced):
            self.verifies_only_with_supplied_anchor(self.anchor1)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0] & os.O_ACCMODE, os.O_RDONLY)

    def test_read_only_verifier_refuses_publication(self):
        viewer = CheckpointWitnessLog(
            self.path, observer_public_key=self.key.public_key(),
            observer_id="x1-synthetic-observer", worker_id="lenovo-synthetic-worker")
        with self.assertRaises(UnsafeWitness):
            viewer.publish(self.checkpoint(2, SECOND, checkpoint_sha256(self.cp1)),
                           trusted_anchor=self.anchor1)

    def test_symlink_and_hardlink_denied(self):
        self.path.rename(self.root / "original")
        self.path.symlink_to(self.root / "original")
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(self.anchor1)
        self.path.unlink()
        os.link(self.root / "original", self.path)
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(self.anchor1)

    def test_world_readable_file_denied(self):
        os.chmod(self.path, 0o644)
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(self.anchor1)

    def test_world_searchable_parent_denied(self):
        os.chmod(self.root, 0o755)
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(self.anchor1)

    def test_cannot_create_over_existing_file(self):
        with self.assertRaises(UnsafeWitness):
            self.log.publish(self.cp1, trusted_anchor=None, genesis=True)

    def test_concurrent_same_anchor_serializes_and_fences_stale_writer(self):
        cp2 = self.checkpoint(2, SECOND, checkpoint_sha256(self.cp1))
        statuses = []
        lock = threading.Lock()
        def attempt():
            try:
                self.log.publish(cp2, trusted_anchor=self.anchor1)
                answer = "published"
            except UnsafeWitness:
                answer = "denied"
            with lock:
                statuses.append(answer)
        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=7)
        self.assertEqual(statuses.count("published"), 1)
        self.assertEqual(statuses.count("denied"), 7)

    def test_genesis_cannot_use_unpinned_predecessor(self):
        with self.assertRaises(UnsafeWitness):
            self.log.publish(self.cp1, trusted_anchor=self.anchor1, genesis=True)

    def test_missing_file_refuses_silent_reset(self):
        self.path.unlink()
        with self.assertRaises(UnsafeWitness):
            self.verifies_only_with_supplied_anchor(self.anchor1)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
