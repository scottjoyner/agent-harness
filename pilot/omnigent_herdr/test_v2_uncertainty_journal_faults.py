"""Controlled lost journal ACKs, source uncertainties and read-only recovery.

Only disposable worker-side SQLite fixtures. The CLI fault flags exit the
ONE one-shot test Python process, never fleet services or model workers.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path

import test_v2_uncertainty_journal as journal_fixture
from v2_uncertainty_journal import UnsafeUncertaintyJournal


class LostJournalAckTests(unittest.TestCase):
    def setUp(self):
        base = journal_fixture.DurableWorkerUncertaintyTests(
            methodName="test_initial_hold_persisted_and_no_dispatch")
        base.setUp()
        self.addCleanup(base.doCleanups)
        self.fx = base
        self.root = base.root
        self.initial = base.initial
        self.cli_path = Path(__file__).with_name("v2_uncertainty_journal.py")
        self.env = {**os.environ, "HOME": str(base.fixture.home)}

    def cli(self, command, *, head=None, reply=None, fault="none",
            payload=None, context=None):
        anchor = self.initial if head is None else head
        args = [
            sys.executable, "-B", str(self.cli_path),
            "--root", str(self.root),
            "--command", command,
            "--context-sha256", context if context is not None else self.fx.ctxhash,
            "--head", f"{anchor[0]}:{anchor[1]}",
            "--research-fault", fault]
        if payload is None:
            payload = {"source_reply": reply}
        result = subprocess.run(
            args, input=json.dumps(payload)+"\n",
            capture_output=True, text=True,
            timeout=12, env=self.env)
        response = json.loads(result.stdout) if result.stdout else None
        return result.returncode, response

    def test_cli_crash_before_append_stores_no_record(self):
        rc, response = self.cli(
            "observe", reply=self.fx.fixture.recovered,
            fault="crash-before-journal-commit")
        self.assertEqual(rc, 96)
        self.assertIsNone(response)
        self.assertEqual(self.fx.inspect()["status"], "UNCERTAIN_HOLD")
        with sqlite3.connect(self.fx.journal.path) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM observations").fetchone()[0], 0)

    def test_cli_crash_after_commit_loses_ack_but_is_reconcilable(self):
        rc, response = self.cli(
            "observe", reply=self.fx.fixture.recovered,
            fault="crash-after-journal-commit")
        self.assertEqual(rc, 97)
        self.assertIsNone(response)
        found_rc, recovered = self.cli(
            "reconcile", reply=self.fx.fixture.recovered)
        self.assertEqual(found_rc, 0)
        self.assertEqual(recovered["status"], "SOURCE_LOCAL_COMMITTED_HOLD")
        self.assertTrue(recovered["source_local_commit_verified"])
        self.assertFalse(recovered["automatic_retry_authorized"])
        self.assertFalse(recovered["agent_dispatch_authorized"])
        self.assertEqual(recovered["journal_head"][0], 1)
        with sqlite3.connect(self.fx.journal.path) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM observations").fetchone()[0], 1)
        rc, again = self.cli(
            "reconcile", reply=self.fx.fixture.recovered)
        self.assertEqual(rc, 0)
        self.assertEqual(again, recovered)

    def test_lost_uncertain_ack_is_still_hold_without_retry(self):
        rc, _ = self.cli(
            "observe", reply=None, fault="crash-after-journal-commit")
        self.assertEqual(rc, 97)
        rc, response = self.cli("reconcile", reply=None)
        self.assertEqual(rc, 0)
        self.assertEqual(response["status"], "UNCERTAIN_HOLD")
        self.assertFalse(response["automatic_retry_authorized"])
        self.assertFalse(response["automatic_refund_authorized"])

    def test_reconcile_cannot_invent_an_uncommitted_journal_write(self):
        rc, resp = self.cli(
            "reconcile", reply=self.fx.fixture.recovered)
        self.assertEqual(rc, 2)
        self.assertEqual(resp["status"], "QUARANTINED_HOLD")
        self.assertFalse(resp["agent_dispatch_authorized"])
        self.assertEqual(self.fx.inspect()["status"], "UNCERTAIN_HOLD")

    def test_uncommitted_fault_followed_by_reconcile_fails_closed(self):
        rc, _ = self.cli(
            "observe", reply=self.fx.fixture.recovered,
            fault="crash-before-journal-commit")
        self.assertEqual(rc, 96)
        rc, resp = self.cli("reconcile", reply=self.fx.fixture.recovered)
        self.assertEqual(rc, 2)
        self.assertEqual(resp["status"], "QUARANTINED_HOLD")

    def test_reconcile_wrong_original_source_receipt_denied(self):
        self.fx.observe(self.fx.fixture.recovered)
        bad = deepcopy(self.fx.fixture.recovered)
        bad["receipt"]["signature"] = "forged"
        rc, resp = self.cli("reconcile", reply=bad)
        self.assertEqual(rc, 2)
        self.assertFalse(resp["source_local_commit_verified"])
        self.assertFalse(resp["automatic_retry_authorized"])

    def test_reconcile_source_identity_and_intent_must_be_exact(self):
        self.fx.observe(self.fx.fixture.recovered)
        for field, bad_value in (
            ("source_public_base64", "different"),
            ("mode", "synthetic_resource_commit"),
        ):
            with self.subTest(field=field):
                bad=deepcopy(self.fx.fixture.recovered)
                bad[field]=bad_value
                rc,_=self.cli("reconcile", reply=bad)
                self.assertEqual(rc,2)

    def test_reconcile_rejects_stale_prior_journal_head(self):
        first = self.fx.observe(None)
        before_commit=tuple(first["journal_head"])
        second=self.fx.observe(self.fx.fixture.recovered, head=before_commit)
        rc, resp=self.cli(
            "reconcile", head=self.initial,
            reply=self.fx.fixture.recovered)
        self.assertEqual(rc,2)
        self.assertEqual(resp["status"],"QUARANTINED_HOLD")
        rc, correct=self.cli(
            "reconcile", head=before_commit,
            reply=self.fx.fixture.recovered)
        self.assertEqual(rc,0)
        self.assertEqual(correct["journal_head"],second["journal_head"])

    def test_reconcile_cannot_rewrite_a_different_later_observation(self):
        first = self.fx.observe(None)
        second = self.fx.observe(
            self.fx.fixture.recovered,
            head=tuple(first["journal_head"]))
        rc,_=self.cli("reconcile", head=self.initial, reply=None)
        self.assertEqual(rc,2)
        self.assertEqual(self.fx.inspect(tuple(second["journal_head"]))[
            "status"],"SOURCE_LOCAL_COMMITTED_HOLD")

    def test_cli_duplicate_observe_after_lost_ack_denied(self):
        rc,_=self.cli(
            "observe", reply=self.fx.fixture.recovered,
            fault="crash-after-journal-commit")
        self.assertEqual(rc,97)
        rc,resp=self.cli("observe", reply=self.fx.fixture.recovered)
        self.assertEqual(rc,2)
        self.assertEqual(resp["status"],"QUARANTINED_HOLD")
        self.assertFalse(resp["automatic_retry_authorized"])

    def test_reconcile_is_read_only_many_times(self):
        first=self.fx.observe(self.fx.fixture.recovered)
        with sqlite3.connect(self.fx.journal.path) as db:
            before=db.total_changes
        for _ in range(4):
            rc,resp=self.cli(
                "reconcile", reply=self.fx.fixture.recovered)
            self.assertEqual(rc,0)
            self.assertEqual(resp["journal_head"],first["journal_head"])
        with sqlite3.connect(self.fx.journal.path) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM observations").fetchone()[0],1)

    def test_cli_fault_not_allowed_on_reconcile_or_inspect(self):
        for command in ("inspect", "reconcile"):
            for fault in ("crash-before-journal-commit",
                          "crash-after-journal-commit"):
                with self.subTest(command=command, fault=fault):
                    rc,resp=self.cli(
                        command, reply=self.fx.fixture.recovered, fault=fault)
                    self.assertEqual(rc,2)
                    self.assertFalse(resp["agent_dispatch_authorized"])
                    self.assertFalse(resp["automatic_refund_authorized"])

    def test_cli_bad_context_head_denied(self):
        first=self.fx.observe(self.fx.fixture.recovered)
        rc,_=self.cli(
            "reconcile", reply=self.fx.fixture.recovered,
            context="f"*64)
        self.assertEqual(rc,2)
        rc,_=self.cli(
            "reconcile", reply=self.fx.fixture.recovered,
            head=(0,"f"*64))
        self.assertEqual(rc,2)
        self.assertEqual(self.fx.inspect(tuple(first["journal_head"]))[
            "status"],"SOURCE_LOCAL_COMMITTED_HOLD")

    def test_duplicate_json_fields_denied_before_write(self):
        proc=subprocess.run([
            sys.executable, "-B", str(self.cli_path),
            "--root", str(self.root), "--command", "observe",
            "--context-sha256", self.fx.ctxhash,
            "--head", f"0:{self.fx.ctxhash}"],
            input='{"source_reply":null,"source_reply":null}\n',
            capture_output=True,text=True,env=self.env,timeout=12)
        self.assertEqual(proc.returncode,2)
        self.assertEqual(self.fx.inspect()["status"],"UNCERTAIN_HOLD")

    def test_abort_transaction_insert_does_not_leave_phantom_record(self):
        with sqlite3.connect(self.fx.journal.path) as db:
            db.execute("""CREATE TRIGGER fail_uncertain_insert
                BEFORE INSERT ON observations
                BEGIN
                    SELECT RAISE(ABORT, 'injected local journal disk fault');
                END""")
        rc,resp=self.cli("observe",reply=self.fx.fixture.recovered)
        self.assertEqual(rc,2)
        self.assertFalse(resp["automatic_retry_authorized"])
        with sqlite3.connect(self.fx.journal.path) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM observations").fetchone()[0],0)
        self.assertEqual(self.fx.inspect()["status"],"UNCERTAIN_HOLD")

    def test_reconcile_corrupted_committed_receipt_is_never_accepted(self):
        first=self.fx.observe(self.fx.fixture.recovered)
        with sqlite3.connect(self.fx.journal.path) as db:
            db.execute("UPDATE observations SET source_reply_json='{}'")
        rc,resp=self.cli(
            "reconcile",reply=self.fx.fixture.recovered)
        self.assertEqual(rc,2)
        self.assertFalse(resp["source_local_commit_verified"])
        self.assertFalse(resp["lease_unfence_authorized"])
        with self.assertRaises(UnsafeUncertaintyJournal):
            self.fx.inspect(tuple(first["journal_head"]))

    def test_reconcile_does_not_change_source_local_balance(self):
        self.fx.observe(self.fx.fixture.recovered)
        before=self.fx.fixture.source.inspect(
            trusted_head=tuple(self.fx.fixture.committed["head"]))
        rc,recovered=self.cli("reconcile",reply=self.fx.fixture.recovered)
        self.assertEqual(rc,0)
        after=self.fx.fixture.source.inspect(
            trusted_head=tuple(self.fx.fixture.committed["head"]))
        self.assertEqual(before["balance"],1)
        self.assertEqual(after["balance"],1)
        self.assertEqual(before["receipt"],after["receipt"])
        self.assertEqual(recovered["decision"],"HOLD")


if __name__=="__main__":
    unittest.main()
