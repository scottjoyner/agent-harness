"""Private HOLD-only recovery journal, never a source/worker dispatch path."""
from __future__ import annotations

import base64
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import test_v2_cross_host_uncertainty as synthetic_fixture
from observer_checkpoint import ZERO
from v2_uncertainty_journal import (
    UnsafeUncertaintyJournal, SyntheticWorkerUncertaintyJournal,
    context_digest,
)


def _raw(pub):
    return base64.b64encode(pub.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )).decode("ascii")


class DurableWorkerUncertaintyTests(unittest.TestCase):
    def setUp(self):
        f = synthetic_fixture.FailClosedSourceRecoveryTests(
            methodName="test_signed_read_only_recovery_is_source_local_hold")
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.fixture = f
        self.root = f.home / ".cache" / "og-v2-uncertain-Ab91Qc"
        self.root.mkdir(mode=0o700)
        self.ctx = {
            "schema": 1, "kind": "synthetic_v2_uncertain_worker_intent",
            "operation_id": "1"*32,
            "worker_public_base64": _raw(f.worker.public_key()),
            "worker_pin": f.params["trusted_worker_pin"],
            "observer_pin": f.params["trusted_observer_pin"],
            "source_public_base64": _raw(f.source.signer.public_key()),
            "source_pin": f.params["trusted_source_pin"],
            "worker_id": "worker", "observer_id": "observer",
            "worker_outbox_head": list(f.worker_head),
            "worker_before_state": f.before,
            "worker_after_state": f.after,
            "prior_source_head": [0, ZERO],
        }
        self.ctxhash = context_digest(self.ctx)
        self.journal = SyntheticWorkerUncertaintyJournal(
            self.root, create=True, context=self.ctx,
            worker_publication=f.publication)
        self.initial = (0, self.ctxhash)

    def reopen(self):
        return SyntheticWorkerUncertaintyJournal(self.root)

    def observe(self, source_reply, head=None):
        return self.reopen().observe(
            source_reply, expected_context_sha256=self.ctxhash,
            trusted_prior_journal_head=self.initial if head is None else head)

    def inspect(self, head=None):
        return self.reopen().inspect(
            expected_context_sha256=self.ctxhash,
            trusted_journal_head=self.initial if head is None else head)

    def test_initial_hold_persisted_and_no_dispatch(self):
        a = self.inspect()
        self.assertEqual(a["status"], "UNCERTAIN_HOLD")
        self.assertEqual(a["journal_head"], list(self.initial))
        self.assertFalse(a["agent_dispatch_authorized"])
        self.assertFalse(a["automatic_retry_authorized"])
        self.assertFalse(a["automatic_refund_authorized"])
        self.assertFalse(a["lease_unfence_authorized"])
        self.assertEqual(
            (self.root / "worker-uncertainty.sqlite3").stat().st_mode & 0o777,
            0o600)

    def test_lost_reply_uncertain_then_signed_source_verified(self):
        uncertain = self.observe(None)
        self.assertEqual(uncertain["status"], "UNCERTAIN_HOLD")
        uncertain_head = tuple(uncertain["journal_head"])
        self.assertEqual(self.inspect(uncertain_head)["status"], "UNCERTAIN_HOLD")
        committed = self.observe(self.fixture.recovered, head=uncertain_head)
        self.assertEqual(committed["status"], "SOURCE_LOCAL_COMMITTED_HOLD")
        self.assertTrue(committed["source_local_commit_verified"])
        self.assertFalse(committed["automatic_retry_authorized"])
        self.assertFalse(committed["worker_source_distributed_atomicity_verified"])
        final = self.inspect(tuple(committed["journal_head"]))
        self.assertEqual(final["status"], "SOURCE_LOCAL_COMMITTED_HOLD")

    def test_missing_source_record_remains_uncertain(self):
        message = {"decision": "HOLD", "agent_dispatch_authorized": False,
                   "provider_requests_forwarded": 0,
                   "error": "resource source denied"}
        out = self.observe(message)
        self.assertEqual(out["status"], "UNCERTAIN_HOLD")

    def test_malformed_recovery_is_terminal_quarantine(self):
        bad = self.observe({"invalid": "reply"})
        self.assertEqual(bad["status"], "QUARANTINED_HOLD")
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.observe(self.fixture.recovered, head=tuple(bad["journal_head"]))
        self.assertEqual(self.inspect(tuple(bad["journal_head"]))["status"],
                         "QUARANTINED_HOLD")

    def test_forged_source_receipt_is_quarantined(self):
        bad = deepcopy(self.fixture.recovered)
        bad["receipt"]["payload"]["operation_id"] = "f"*32
        self.assertEqual(self.observe(bad)["status"], "QUARANTINED_HOLD")

    def test_source_after_commit_disallows_downgrade(self):
        accepted = self.observe(self.fixture.recovered)
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.observe(None, tuple(accepted["journal_head"]))

    def test_source_after_commit_disallows_conflicting_receipt(self):
        accepted = self.observe(self.fixture.recovered)
        fake = deepcopy(self.fixture.recovered)
        fake["receipt"]["signature"] = "forged"
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.observe(fake, tuple(accepted["journal_head"]))

    def test_source_accepted_readback_does_not_write(self):
        accepted = self.observe(self.fixture.recovered)
        head = tuple(accepted["journal_head"])
        for _ in range(3):
            self.assertEqual(self.inspect(head)["journal_head"], list(head))
        with sqlite3.connect(self.journal.path) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM observations").fetchone()[0], 1)

    def test_stale_worker_journal_head_denied(self):
        event = self.observe(None)
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.observe(self.fixture.recovered, self.initial)
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.inspect(self.initial)
        self.assertEqual(self.inspect(tuple(event["journal_head"]))["status"],
                         "UNCERTAIN_HOLD")

    def test_bad_external_context_pin_denied(self):
        event = self.observe(None)
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.reopen().inspect(
                expected_context_sha256="f"*64,
                trusted_journal_head=tuple(event["journal_head"]))

    def test_mutated_context_key_and_scope_denied(self):
        for field in ("worker_pin", "source_pin", "operation_id"):
            with self.subTest(field=field):
                ctx = {**self.ctx, field: "f"*(32 if field == "operation_id" else 64)}
                # Illegal changed public pin or operation signature
                # cannot be confused with the originally signed worker.
                if field == "operation_id":
                    fresh_root = (self.fixture.home / ".cache" /
                                  "og-v2-uncertain-Zz88Xx")
                    fresh_root.mkdir(mode=0o700)
                    with self.assertRaises(UnsafeUncertaintyJournal):
                        SyntheticWorkerUncertaintyJournal(
                            fresh_root, create=True, context=ctx,
                            worker_publication=self.fixture.publication)
                    fresh_root.rmdir()
                else:
                    with self.assertRaises(UnsafeUncertaintyJournal):
                        context_digest(ctx)

    def test_malformed_base64_root_is_rejected_not_unhandled(self):
        context = {**self.ctx, "source_public_base64": "?"*44}
        with self.assertRaises(UnsafeUncertaintyJournal):
            context_digest(context)

    def test_research_tempfile_underscore_allowed_no_path_escape(self):
        # tempfile's random candidate names may include underscores;
        # all roots remain directly under the private local .cache.
        root = self.fixture.home / ".cache" / "og-v2-uncertain-Q_88X1_"
        root.mkdir(mode=0o700)
        test = SyntheticWorkerUncertaintyJournal(
            root, create=True, context=self.ctx,
            worker_publication=self.fixture.publication)
        self.assertEqual(test.inspect(
            expected_context_sha256=self.ctxhash,
            trusted_journal_head=self.initial)["status"], "UNCERTAIN_HOLD")

    def test_initial_publication_signature_substitution_denied(self):
        bad = deepcopy(self.fixture.publication)
        bad["payload"]["outbox_event"]["operation_id"] = "f"*32
        fresh_root = self.fixture.home / ".cache" / "og-v2-uncertain-Zz8Xy1"
        fresh_root.mkdir(mode=0o700)
        with self.assertRaises(UnsafeUncertaintyJournal):
            SyntheticWorkerUncertaintyJournal(
                fresh_root, create=True, context=self.ctx,
                worker_publication=bad)

    def test_forbidden_non_private_root(self):
        self.root.chmod(0o755)
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.inspect()

    def test_corrupt_stored_source_evidence_detected_on_reopen(self):
        good = self.observe(self.fixture.recovered)
        with sqlite3.connect(self.journal.path) as db:
            db.execute("UPDATE observations SET verdict_json='{}' WHERE ordinal=1")
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.inspect(tuple(good["journal_head"]))

    def test_corrupt_signed_worker_detected_on_reopen(self):
        with sqlite3.connect(self.journal.path) as db:
            db.execute("UPDATE source_intent SET signed_worker_json='{}'")
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.inspect()

    def test_corrupt_observation_link_detected(self):
        event = self.observe(None)
        with sqlite3.connect(self.journal.path) as db:
            db.execute(
                "UPDATE observations SET previous_sha256=? WHERE ordinal=1",
                ("f"*64,))
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.inspect(tuple(event["journal_head"]))

    def test_removed_or_symlinked_db_denied_not_recreated(self):
        self.journal.path.rename(self.root / "old.sqlite3")
        self.journal.path.symlink_to(self.root / "old.sqlite3")
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.inspect()
        self.journal.path.unlink()
        with self.assertRaises((UnsafeUncertaintyJournal, OSError)):
            self.inspect()

    def test_db_hardlink_denied(self):
        os.link(self.journal.path, self.root / "duplicate.sqlite3")
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.inspect()

    def test_concurrent_only_one_writer_accepts_same_head(self):
        gate = threading.Barrier(5)
        outcomes = []
        guard = threading.Lock()
        def writer():
            gate.wait(timeout=8)
            try:
                self.observe(self.fixture.recovered)
                status = "committed"
            except (UnsafeUncertaintyJournal, sqlite3.Error):
                status = "denied"
            with guard:
                outcomes.append(status)
        threads = [threading.Thread(target=writer) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
        self.assertEqual(outcomes.count("committed"), 1)
        self.assertEqual(outcomes.count("denied"), 4)

    def test_rollback_old_db_requires_retained_head(self):
        old = self.root / "oldvalid.sqlite3"
        shutil.copy2(self.journal.path, old)
        verified = self.observe(self.fixture.recovered)
        newer_head = tuple(verified["journal_head"])
        shutil.copy2(old, self.journal.path)
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.inspect(newer_head)
        # Required counterexample: the same fleet admin reset the old
        # journal AND the caller's expected head.
        self.assertEqual(self.inspect(self.initial)["status"], "UNCERTAIN_HOLD")

    def test_duplicate_initial_journal_cannot_be_overwritten(self):
        with self.assertRaises(FileExistsError):
            SyntheticWorkerUncertaintyJournal(
                self.root, create=True, context=self.ctx,
                worker_publication=self.fixture.publication)

    def test_successful_source_receipt_never_authorizes_execution(self):
        verified = self.observe(self.fixture.recovered)
        fields = (
            "agent_dispatch_authorized", "model_egress_authorized",
            "automatic_retry_authorized", "automatic_refund_authorized",
            "lease_unfence_authorized", "worker_source_distributed_atomicity_verified",
        )
        self.assertTrue(all(verified[k] is False for k in fields))


if __name__ == "__main__":
    unittest.main()
