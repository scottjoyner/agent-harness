#!/usr/bin/env python3
"""Physical subprocess/restart test: disposable V2 observer SQLite inbox.

Each state transition occurs in a DIFFERENT short-lived process using
the same in-RAM fixture signer supplied by a test supervisor; keys are
not persisted. Scratch worker V2 debit/outbox and observer SQLite DB
are separate. Nothing connects to live agents, providers or real repos.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import secrets
import shutil
import socket
import sqlite3
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from observer_checkpoint import ZERO, public_fingerprint
from v2_outbox_publication import worker_publish, worker_sign_delivery
from v2_durable_observer_inbox import (
    SyntheticV2DurableObserverInbox, UnsafeObserverInbox, _digest,
)

WORKTREE = hashlib.sha256(b"v2-inbox-subprocess-disposable").hexdigest()
OPERATION = "1"*32
WORKER = "synthetic-worker"
OBSERVER = "synthetic-observer"


def _child(path, observer_signer, worker_public, command, kwargs, result):
    try:
        instance = SyntheticV2DurableObserverInbox(
            Path(path), observer_signer=observer_signer,
            worker_public_key=worker_public,
            observer_id=OBSERVER, worker_id=WORKER)
        if command == "inspect":
            answer = {"status": "read", "state": instance.inspect(**kwargs)}
        elif command == "receive":
            head = instance.receive(**kwargs)
            answer = {"status": "published", "head": head}
        elif command == "ack":
            head, ack = instance.issue_ack(**kwargs)
            answer = {"status": "signed_ack_fenced", "head": head, "ack": ack}
        elif command == "delivery":
            head = instance.confirm(**kwargs)
            answer = {"status": "delivered", "head": head}
        elif command == "quarantine":
            head = instance.quarantine_unverified(**kwargs)
            answer = {"status": "quarantined", "head": head}
        else:
            raise UnsafeObserverInbox("disallowed fixture operation")
        result.send(answer)
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
        result.send({"status": "denied"})
    finally:
        result.close()


def physical() -> dict:
    report = {
        "schema": 1, "mode": "synthetic_restart_durable_v2_observer_inbox",
        "observed_host": socket.gethostname(),
        "worker_v2_atomic_outbox_bound": False,
        "publication_fence_survives_process_restart": False,
        "signed_ack_fence_survives_process_restart": False,
        "lost_ack_cannot_be_reissued": False,
        "forged_delivery_cannot_clear_fence": False,
        "valid_delivery_survives_process_restart": False,
        "stale_anchor_denied": False,
        "newer_anchor_detects_old_valid_db": False,
        "old_db_and_old_anchor_still_match": False,
        "unverified_publication_quarantine_survived_restart": False,
        "signing_keys_persisted": False,
        "independent_worker_identity_verified": False,
        "durable_external_anchor_verified": False,
        "source_transaction_attested": False,
        "rollback_resistance_verified": False,
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
        with tempfile.TemporaryDirectory(
                prefix="og-v2-durable-inbox-", dir=cache) as folder:
            root = Path(folder)
            os.chmod(root, 0o700)
            worker_path = root / "worker.sqlite3"
            inbox_path = root / "observer.sqlite3"
            lease = AtomicSyntheticLeaseOutbox(
                worker_path, secrets.token_bytes(32),
                now=lambda: 1000, create=True)
            token = lease.mint(
                uid=os.getuid(), owner="fixture-owner",
                session="fixture-session",
                worktree_sha256=WORKTREE,
                model_pin="kilo/kilo-auto/free")
            before = lease.state_digest()
            request = {
                "schema": 1, "op": "synthetic_receipt",
                "token": token, "seq": 1,
            }
            debit = lease.check(
                request, peer_uid=os.getuid(),
                expected_owner="fixture-owner",
                expected_session="fixture-session",
                expected_worktree_sha256=WORKTREE,
                expected_model_pin="kilo/kilo-auto/free",
                operation_id=OPERATION)
            after = lease.state_digest()
            head = (debit.get("atomic_outbox_record"),
                    debit.get("atomic_outbox_sha256"))
            report["worker_v2_atomic_outbox_bound"] = (
                debit.get("status") == "synthetic_receipt_only"
                and head[0] == 1
                and lease.inspect(trusted_anchor=head)[
                    "lease_state_sha256"] == after)
            worker_key = Ed25519PrivateKey.generate()
            observer_key = Ed25519PrivateKey.generate()
            observation = worker_publish(
                signer=worker_key, worker_id=WORKER,
                observer_id=OBSERVER,
                observer_fingerprint=public_fingerprint(
                    observer_key.public_key()),
                exported_head=lease.export_head_event(trusted_anchor=head))
            SyntheticV2DurableObserverInbox(
                inbox_path, observer_signer=observer_key,
                worker_public_key=worker_key.public_key(),
                observer_id=OBSERVER, worker_id=WORKER,
                initial_state=before, create=True)
            with sqlite3.connect(inbox_path) as db:
                genesis_mark = json.loads(db.execute(
                    "SELECT marker FROM metadata").fetchone()[0])
            anchor0 = (0, _digest(genesis_mark))
            old_valid_db = root / "valid-old-observer-image"
            shutil.copy2(inbox_path, old_valid_db)

            def stage(command, kwargs):
                rx, tx = ctx.Pipe(duplex=False)
                process = ctx.Process(
                    target=_child, args=(
                        str(inbox_path), observer_key,
                        worker_key.public_key(), command, kwargs, tx),
                    name="disposable-v2-observer-inbox-fixture")
                process.start()
                tx.close()
                try:
                    if not rx.poll(9):
                        raise TimeoutError("synthetic observer did not respond")
                    response = rx.recv()
                    process.join(timeout=4)
                    if process.is_alive() or process.exitcode != 0:
                        raise RuntimeError("disposable observer did not exit")
                    return response
                finally:
                    rx.close()
                    if process.is_alive():
                        process.terminate()  # Only own disposable child PID.
                        process.join(timeout=3)

            received = stage("receive", {
                "publication": observation,
                "trusted_anchor": anchor0,
                "expected_operation_id": OPERATION,
                "expected_outbox_head": head,
                "expected_after_state": after})
            anchor1 = tuple(received.get("head", []))
            p1 = stage("inspect", {"trusted_anchor": anchor1})
            report["publication_fence_survives_process_restart"] = (
                received.get("status") == "published"
                and p1.get("status") == "read"
                and p1["state"]["stage"] == "publication_fenced"
                and p1["state"]["uncertain"] is True)
            signed = stage("ack", {"trusted_anchor": anchor1})
            anchor2 = tuple(signed.get("head", []))
            p2 = stage("inspect", {"trusted_anchor": anchor2})
            report["signed_ack_fence_survives_process_restart"] = (
                signed.get("status") == "signed_ack_fenced"
                and p2.get("status") == "read"
                and p2["state"]["stage"] == "ack_signed_fenced"
                and p2["state"]["uncertain"] is True
                and p2["state"]["signed_delivery_present"] is False)
            report["lost_ack_cannot_be_reissued"] = (
                stage("ack", {"trusted_anchor": anchor2})["status"] == "denied"
                and stage("ack", {"trusted_anchor": anchor1})["status"] == "denied")
            valid_delivery = worker_sign_delivery(
                signer=worker_key, publication=observation,
                observer_ack=signed["ack"],
                observer_public_key=observer_key.public_key(),
                trusted_observer_fingerprint=public_fingerprint(
                    observer_key.public_key()),
                expected_generation=2, previous_checkpoint_sha256=ZERO)
            tampered = {
                **valid_delivery,
                "payload": {
                    **valid_delivery["payload"],
                    "observer_ack_sha256": "f"*64,
                },
            }
            report["forged_delivery_cannot_clear_fence"] = (
                stage("delivery", {
                    "delivery": tampered, "trusted_anchor": anchor2,
                })["status"] == "denied"
                and stage("inspect", {"trusted_anchor": anchor2})[
                    "state"]["uncertain"] is True)
            delivered = stage("delivery", {
                "delivery": valid_delivery, "trusted_anchor": anchor2})
            anchor3 = tuple(delivered.get("head", []))
            last = stage("inspect", {"trusted_anchor": anchor3})
            report["valid_delivery_survives_process_restart"] = (
                delivered.get("status") == "delivered"
                and last.get("status") == "read"
                and last["state"]["stage"] == "delivered"
                and last["state"]["agent_dispatch_authorized"] is False)
            report["stale_anchor_denied"] = (
                stage("inspect", {"trusted_anchor": anchor2})[
                    "status"] == "denied")
            shutil.copy2(old_valid_db, inbox_path)
            report["newer_anchor_detects_old_valid_db"] = (
                stage("inspect", {"trusted_anchor": anchor3})[
                    "status"] == "denied")
            report["old_db_and_old_anchor_still_match"] = (
                stage("inspect", {"trusted_anchor": anchor0})[
                    "state"]["revision"] == 0)
            altered_publication = {
                **observation,
                "payload": {
                    **observation["payload"],
                    "outbox_record_sha256": "f"*64,
                },
            }
            quarantined = stage("quarantine", {
                "claimed_publication": altered_publication,
                "expected_operation_id": OPERATION,
                "trusted_anchor": anchor0,
            })
            quarantine_head = tuple(quarantined.get("head", []))
            quarantine_read = stage("inspect", {
                "trusted_anchor": quarantine_head})
            report["unverified_publication_quarantine_survived_restart"] = (
                quarantined.get("status") == "quarantined"
                and quarantine_read.get("status") == "read"
                and quarantine_read["state"]["stage"] == "quarantined_untrusted"
                and quarantine_read["state"]["uncertain"] is True
                and stage("ack", {
                    "trusted_anchor": quarantine_head})["status"] == "denied")
            if not all(report[name] for name in (
                "worker_v2_atomic_outbox_bound",
                "publication_fence_survives_process_restart",
                "signed_ack_fence_survives_process_restart",
                "lost_ack_cannot_be_reissued",
                "forged_delivery_cannot_clear_fence",
                "valid_delivery_survives_process_restart",
                "stale_anchor_denied",
                "newer_anchor_detects_old_valid_db",
                "old_db_and_old_anchor_still_match",
                "unverified_publication_quarantine_survived_restart",
            )):
                report["errors"].append("V2 durable observer fixture incomplete")
    except (OSError, ValueError, KeyError, RuntimeError, TypeError,
            TimeoutError, EOFError, sqlite3.Error):
        report["errors"].append("durable V2 observer fixture failed closed")
    return report


def exit_status(report: dict) -> int:
    required = (
        "worker_v2_atomic_outbox_bound",
        "publication_fence_survives_process_restart",
        "signed_ack_fence_survives_process_restart",
        "lost_ack_cannot_be_reissued",
        "forged_delivery_cannot_clear_fence",
        "valid_delivery_survives_process_restart",
        "stale_anchor_denied",
        "newer_anchor_detects_old_valid_db",
        "old_db_and_old_anchor_still_match",
        "unverified_publication_quarantine_survived_restart",
    )
    denied = (
        "signing_keys_persisted", "independent_worker_identity_verified",
        "durable_external_anchor_verified", "source_transaction_attested",
        "rollback_resistance_verified", "agent_dispatch_authorized",
        "model_egress_authorized", "used_real_repository",
    )
    return 0 if (
        all(report.get(name) is True for name in required)
        and all(report.get(name) is False for name in denied)
        and report.get("provider_requests_forwarded") == 0
        and not report.get("errors")
    ) else 2


if __name__ == "__main__":
    state = physical()
    print(json.dumps(state, indent=2, sort_keys=True))
    raise SystemExit(exit_status(state))
