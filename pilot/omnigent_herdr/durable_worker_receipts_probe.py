#!/usr/bin/env python3
"""Physical cooperative subprocess / restart synthetic worker receipt proof.

All databases, tokens and keys belong to one disposable test root.
Demonstrates the unclosed crash gap between lease debit and receipt DB.
In-memory private signing keys are passed only to short-lived forked
children; no models, real repositories, or outside providers are touched.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import secrets
import shutil
import socket
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from durable_lease_store import DurableSyntheticLeaseGate
from durable_worker_receipts import (
    SyntheticWorkerReceiptJournal, UnsafeWorkerReceipt,
)
from observer_checkpoint import ZERO, public_fingerprint, _json_bytes

WORKTREE = hashlib.sha256(b"disposable-durable-worker-worktree").hexdigest()
OPID = "1" * 32
PREPARE = hashlib.sha256(b"synthetic signed observer prepare").hexdigest()
ACK = hashlib.sha256(b"synthetic observer acknowledgment").hexdigest()
CHECKPOINT = hashlib.sha256(b"synthetic observer checkpoint").hexdigest()


def _child(path, lease_path, hmac_key, signer, observer_pin, cmd,
           payload, send):
    try:
        journal = SyntheticWorkerReceiptJournal(
            Path(path), worker_public_key=signer.public_key(),
            worker_id="synthetic-lenovo-worker",
            observer_id="synthetic-x1-observer",
            observer_fingerprint=observer_pin, signer=signer)
        if cmd == "inspect":
            answer = {
                "status": "inspected",
                "proof": journal.inspect(
                    trusted_anchor=tuple(payload["anchor"]),
                    observed_worker_state_sha256=payload.get("worker_state")),
            }
        elif cmd == "append_commit":
            anchor, signed = journal.append(
                kind="synthetic_worker_commit",
                fields=payload["fields"],
                trusted_anchor=tuple(payload["anchor"]))
            answer = {"status": "committed_to_receipt_journal",
                      "anchor": anchor, "kind": signed["payload"]["kind"]}
        elif cmd == "append_delivery":
            anchor, signed = journal.append(
                kind="synthetic_worker_delivery",
                fields=payload["fields"],
                trusted_anchor=tuple(payload["anchor"]))
            answer = {"status": "signed_delivery_saved",
                      "anchor": anchor, "kind": signed["payload"]["kind"]}
        elif cmd == "lease_debit":
            gate = DurableSyntheticLeaseGate(
                Path(lease_path), hmac_key, now=lambda: 1000)
            response = gate.check(
                payload["request"],
                peer_uid=os.getuid(), expected_owner="synthetic-owner",
                expected_session="synthetic-session",
                expected_worktree_sha256=WORKTREE,
                expected_model_pin="kilo/kilo-auto/free")
            answer = {
                "status": response["status"],
                "receipt_number": response.get("receipt_number"),
                "state_sha256": gate.state_digest(),
                "agent_dispatch_authorized": False,
            }
        else:
            raise UnsafeWorkerReceipt("unknown synthetic command")
        send.send(answer)
    except Exception:
        send.send({"status": "denied", "agent_dispatch_authorized": False})
    finally:
        send.close()


def physical() -> dict:
    report = {
        "schema": 1, "observed_host": socket.gethostname(),
        "mode": "synthetic_restart_worker_signed_receipt_journal",
        "unlogged_lease_debit_detected": False,
        "signed_commit_survives_worker_process_restart": False,
        "commit_pending_delivery_fences_second_commit": False,
        "matching_signed_delivery_survives_restart": False,
        "stale_anchor_replay_denied": False,
        "rolled_back_worker_state_mismatch_detected": False,
        "older_receipt_journal_denied_with_newer_anchor": False,
        "older_receipt_journal_with_older_anchor_still_valid": False,
        "wrong_worker_key_denied_on_restart": False,
        "independent_worker_key_custody_verified": False,
        "externally_durable_anchor_verified": False,
        "atomic_lease_receipt_commit_verified": False,
        "source_authority_verified": False,
        "agent_dispatch_authorized": False,
        "model_egress_authorized": False,
        "provider_requests_forwarded": 0,
        "used_real_repository": False,
        "errors": [],
    }
    ctx = mp.get_context("fork")
    try:
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="og-worker-signed-ledger-",
                                         dir=cache) as folder:
            root = Path(folder)
            os.chmod(root, 0o700)
            leasedb = root / "synthetic-lease.sqlite3"
            receipt_db = root / "worker-receipts.sqlite3"
            hmac_key = secrets.token_bytes(32)
            lease = DurableSyntheticLeaseGate(
                leasedb, hmac_key, now=lambda: 1000, create=True)
            token = lease.mint(
                uid=os.getuid(), owner="synthetic-owner",
                session="synthetic-session",
                worktree_sha256=WORKTREE, model_pin="kilo/kilo-auto/free")
            before = lease.state_digest()
            request = {
                "schema": 1, "op": "synthetic_receipt", "token": token,
                "seq": 1,
            }
            request_sha = hashlib.sha256(_json_bytes(request)).hexdigest()
            signing_key = Ed25519PrivateKey.generate()
            observer_signer = Ed25519PrivateKey.generate()
            observer_pin = public_fingerprint(observer_signer.public_key())
            SyntheticWorkerReceiptJournal(
                receipt_db, worker_public_key=signing_key.public_key(),
                worker_id="synthetic-lenovo-worker",
                observer_id="synthetic-x1-observer",
                observer_fingerprint=observer_pin,
                signer=signing_key, initial_state=before, create=True)
            baseline_lease = root / "valid-old-lease"
            shutil.copy2(leasedb, baseline_lease)

            def step(command, data):
                rx, tx = ctx.Pipe(duplex=False)
                child = ctx.Process(
                    target=_child,
                    args=(str(receipt_db), str(leasedb), hmac_key,
                          signing_key, observer_pin, command, data, tx),
                    name="disposable-synthetic-receipt-process")
                child.start()
                tx.close()
                try:
                    if not rx.poll(8):
                        raise TimeoutError("worker receipt subprocess no response")
                    response = rx.recv()
                    child.join(timeout=3)
                    if child.is_alive() or child.exitcode != 0:
                        raise RuntimeError("worker receipt subprocess did not exit")
                    return response
                finally:
                    rx.close()
                    if child.is_alive():
                        child.terminate()  # Only own disposable fixture child.
                        child.join(timeout=3)

            debit = step("lease_debit", {"request": request})
            after = debit.get("state_sha256")
            gap = step("inspect", {
                "anchor": (0, ZERO), "worker_state": after})
            report["unlogged_lease_debit_detected"] = (
                debit.get("status") == "synthetic_receipt_only"
                and gap.get("status") == "inspected"
                and gap["proof"]["worker_state_matches_supplied_digest"] is False
                and gap["proof"]["commit_pending_delivery"] is False)
            # No automatic retry or refund on this uncertain intermediate
            # condition. Subsequent synthetic signed-commit recovery here is
            # operator/fixture supplied, not independently authorized.
            fields = {
                "worker_id": "synthetic-lenovo-worker",
                "observer_id": "synthetic-x1-observer",
                "operation_id": OPID, "request_sha256": request_sha,
                "prepare_sha256": PREPARE,
                "before_state_sha256": before,
                "after_state_sha256": after, "generation": 2,
                "sequence": 1, "receipt_number": 1,
                "agent_dispatch_authorized": False,
                "provider_requests_forwarded": 0,
            }
            commit = step("append_commit", {
                "anchor": (0, ZERO), "fields": fields})
            head1 = commit.get("anchor")
            pending = step("inspect", {
                "anchor": head1, "worker_state": after})
            report["signed_commit_survives_worker_process_restart"] = (
                commit.get("status") == "committed_to_receipt_journal"
                and pending.get("status") == "inspected"
                and pending["proof"]["record_count"] == 1)
            report["commit_pending_delivery_fences_second_commit"] = (
                pending["proof"]["commit_pending_delivery"] is True
                and step("append_commit", {
                    "anchor": head1,
                    "fields": {**fields,
                               "operation_id": "2"*32,
                               "before_state_sha256": after,
                               "after_state_sha256": "f"*64,
                               "generation": 3},
                })["status"] == "denied")
            backup_receipts = root / "valid-old-receipt-ledger"
            shutil.copy2(receipt_db, backup_receipts)
            delivery_fields = {
                **fields, "observer_ack_sha256": ACK,
                "observer_checkpoint_sha256": CHECKPOINT,
            }
            delivered = step("append_delivery", {
                "anchor": head1, "fields": delivery_fields})
            head2 = delivered.get("anchor")
            verified = step("inspect", {
                "anchor": head2, "worker_state": after})
            report["matching_signed_delivery_survives_restart"] = (
                delivered.get("status") == "signed_delivery_saved"
                and verified.get("status") == "inspected"
                and verified["proof"]["commit_pending_delivery"] is False
                and verified["proof"]["worker_state_matches_supplied_digest"] is True)
            report["stale_anchor_replay_denied"] = (
                step("append_delivery", {
                    "anchor": head1, "fields": delivery_fields})["status"]
                == "denied")
            shutil.copy2(baseline_lease, leasedb)
            old_lease = DurableSyntheticLeaseGate(
                leasedb, hmac_key, now=lambda: 1000).state_digest()
            mismatch = step("inspect", {
                "anchor": head2, "worker_state": old_lease})
            report["rolled_back_worker_state_mismatch_detected"] = (
                mismatch["status"] == "inspected"
                and mismatch["proof"]["worker_state_matches_supplied_digest"] is False)
            shutil.copy2(backup_receipts, receipt_db)
            report["older_receipt_journal_denied_with_newer_anchor"] = (
                step("inspect", {
                    "anchor": head2, "worker_state": after})["status"]
                == "denied")
            old_old = step("inspect", {
                "anchor": head1, "worker_state": after})
            report["older_receipt_journal_with_older_anchor_still_valid"] = (
                old_old["status"] == "inspected"
                and old_old["proof"]["record_count"] == 1)
            try:
                SyntheticWorkerReceiptJournal(
                    receipt_db,
                    worker_public_key=Ed25519PrivateKey.generate().public_key(),
                    worker_id="synthetic-lenovo-worker",
                    observer_id="synthetic-x1-observer",
                    observer_fingerprint=observer_pin)
            except UnsafeWorkerReceipt:
                report["wrong_worker_key_denied_on_restart"] = True

            if not all(report[k] for k in (
                "unlogged_lease_debit_detected",
                "signed_commit_survives_worker_process_restart",
                "commit_pending_delivery_fences_second_commit",
                "matching_signed_delivery_survives_restart",
                "stale_anchor_replay_denied",
                "rolled_back_worker_state_mismatch_detected",
                "older_receipt_journal_denied_with_newer_anchor",
                "older_receipt_journal_with_older_anchor_still_valid",
                "wrong_worker_key_denied_on_restart",
            )):
                report["errors"].append("worker journal evidence incomplete")
    except (OSError, RuntimeError, ValueError, TypeError, TimeoutError, KeyError):
        report["errors"].append("synthetic worker subprocess evidence failed closed")
    return report


def exit_status(report: dict) -> int:
    proof = (
        "unlogged_lease_debit_detected",
        "signed_commit_survives_worker_process_restart",
        "commit_pending_delivery_fences_second_commit",
        "matching_signed_delivery_survives_restart",
        "stale_anchor_replay_denied",
        "rolled_back_worker_state_mismatch_detected",
        "older_receipt_journal_denied_with_newer_anchor",
        "older_receipt_journal_with_older_anchor_still_valid",
        "wrong_worker_key_denied_on_restart",
    )
    not_proven = (
        "independent_worker_key_custody_verified",
        "externally_durable_anchor_verified",
        "atomic_lease_receipt_commit_verified",
        "source_authority_verified",
        "agent_dispatch_authorized", "model_egress_authorized",
        "used_real_repository",
    )
    return 0 if (
        all(report.get(name) is True for name in proof)
        and all(report.get(name) is False for name in not_proven)
        and report.get("provider_requests_forwarded") == 0
        and not report.get("errors")
    ) else 2


if __name__ == "__main__":
    summary = physical()
    print(json.dumps(summary, indent=2, sort_keys=True))
    raise SystemExit(exit_status(summary))
