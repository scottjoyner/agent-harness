#!/usr/bin/env python3
"""Physical subprocess/restart qualification of a synthetic observer journal.

All state is confined to one temporary private directory and the signing
key exists only in the supervisor's RAM. No model, agent or real repository.
This tests cooperative restart fencing, NOT independent high-water custody
or crash-atomic effects across the worker/observer boundary.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import shutil
import socket
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from durable_observer_journal import ResearchObserverJournal, ObserverJournalError

INITIAL = hashlib.sha256(b"isolated-state-before").hexdigest()
COMMITTED = hashlib.sha256(b"isolated-state-after").hexdigest()
ANOTHER = hashlib.sha256(b"isolated-state-second").hexdigest()
REQUEST_HASH = hashlib.sha256(b"non-user-operation").hexdigest()
OP1 = "1" * 32
OP2 = "2" * 32
OP3 = "3" * 32


def _child(location: str, key, op: str, transmit, payload):
    """One short-lived, synthetic observer process per state transition."""
    try:
        from durable_observer_journal import ResearchObserverJournal
        path = Path(location)
        journal = ResearchObserverJournal(
            path, signer=key, observer_id="observer-synthetic",
            worker_id="worker-synthetic", create=(op == "create"),
            initial_state=INITIAL if op == "create" else None)
        if op == "create":
            answer = {"status": "created", "state": journal.state()}
        elif op == "prepare":
            answer = {
                "status": "prepared",
                "prepare": journal.prepare(
                    operation_id=payload["id"],
                    request_sha256=REQUEST_HASH,
                    before_state_sha256=payload["before"]),
                "state": journal.state(),
            }
        elif op == "ack":
            cp, ack = journal.acknowledge(
                prepared=payload["prepare"],
                after_state_sha256=payload["after"],
                externally_observed_sha256=payload["after"])
            answer = {
                "status": "signed_pending",
                "checkpoint": cp, "ack": ack, "state": journal.state(),
            }
        elif op == "confirm":
            journal.confirm_delivery(
                ack=payload["ack"],
                observed_worker_sha256=payload["after"])
            answer = {"status": "confirmed", "state": journal.state()}
        elif op == "abort":
            journal.abort_prepared(
                prepared=payload["prepare"],
                observed_worker_sha256=payload["observed"])
            answer = {"status": "aborted", "state": journal.state()}
        elif op == "read":
            answer = {"status": "read", "state": journal.state()}
        elif op == "wrong_key":
            raise ValueError("must not be used as a command")
        else:
            raise ValueError("unknown fixture command")
        transmit.send(answer)
    except (OSError, ValueError, ObserverJournalError) as exc:
        transmit.send({"status": "denied", "error": type(exc).__name__})
    finally:
        transmit.close()


def physical() -> dict:
    proof = {
        "schema": 1, "observed_host": socket.gethostname(),
        "mode": "synthetic_restart_durable_observer_journal",
        "prepare_fence_survived_restart": False,
        "unmodified_prepare_can_abort": False,
        "signed_unconfirmed_ack_survived_restart": False,
        "tampered_ack_cannot_release_fence": False,
        "correct_ack_can_confirm_after_restart": False,
        "postconfirm_allows_next_prepare": False,
        "restored_old_observer_db_vulnerability_reproduced": False,
        "signing_key_on_disk": False,
        "independent_signer_custody_verified": False,
        "external_highwater_verified": False,
        "agent_dispatch_authorized": False,
        "model_egress_authorized": False,
        "provider_requests_forwarded": 0,
        "used_real_repository": False,
        "errors": [],
    }
    try:
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="og-observer-journal-", dir=cache) as temp:
            root = Path(temp)
            os.chmod(root, 0o700)
            db = root / "observer.sqlite3"
            signer = Ed25519PrivateKey.generate()
            ctx = mp.get_context("fork")

            def invoke(command: str, payload: dict | None = None):
                recv, send = ctx.Pipe(duplex=False)
                child = ctx.Process(
                    target=_child, args=(str(db), signer, command, send, payload or {}),
                    name="disposable-observer-journal-fixture")
                child.start()
                send.close()
                try:
                    if not recv.poll(9):
                        raise TimeoutError("observer child had no receipt")
                    answer = recv.recv()
                    child.join(timeout=4)
                    if child.is_alive() or child.exitcode != 0:
                        raise RuntimeError("observer child failure")
                    return answer
                finally:
                    recv.close()
                    if child.is_alive():
                        child.terminate()  # Only our disposable child.
                        child.join(timeout=3)

            start = invoke("create")
            prepared1 = invoke("prepare", {"id": OP1, "before": INITIAL})
            pending1 = invoke("read")
            rejected_abort = invoke(
                "abort", {"prepare": prepared1.get("prepare"), "observed": COMMITTED})
            proof["prepare_fence_survived_restart"] = (
                start["status"] == "created"
                and prepared1["status"] == "prepared"
                and pending1["state"]["unresolved"] is True
                and rejected_abort["status"] == "denied")
            good_abort = invoke(
                "abort", {"prepare": prepared1.get("prepare"), "observed": INITIAL})
            proof["unmodified_prepare_can_abort"] = (
                good_abort["status"] == "aborted"
                and good_abort["state"]["unresolved"] is False)

            prepared2 = invoke("prepare", {"id": OP2, "before": INITIAL})
            signed = invoke(
                "ack", {"prepare": prepared2.get("prepare"), "after": COMMITTED})
            pending2 = invoke("read")
            proof["signed_unconfirmed_ack_survived_restart"] = (
                signed["status"] == "signed_pending"
                and pending2["state"]["unresolved"] is True
                and pending2["state"]["generation"] == 2
                and pending2["state"]["state_sha256"] == COMMITTED)

            old_snapshot = root / "old-observer-image"
            shutil.copy2(db, old_snapshot)
            tampered_ack = json.loads(json.dumps(signed.get("ack")))
            tampered_ack["payload"]["after_state_sha256"] = "f" * 64
            tampered = invoke(
                "confirm", {"ack": tampered_ack, "after": COMMITTED})
            proof["tampered_ack_cannot_release_fence"] = (
                tampered["status"] == "denied"
                and invoke("read")["state"]["unresolved"] is True)
            confirmed = invoke(
                "confirm", {"ack": signed.get("ack"), "after": COMMITTED})
            proof["correct_ack_can_confirm_after_restart"] = (
                confirmed["status"] == "confirmed"
                and confirmed["state"]["unresolved"] is False)
            third = invoke("prepare", {"id": OP3, "before": COMMITTED})
            proof["postconfirm_allows_next_prepare"] = (
                third["status"] == "prepared"
                and third["state"]["unresolved"] is True)
            shutil.copy2(old_snapshot, db)
            rolled = invoke("read")
            proof["restored_old_observer_db_vulnerability_reproduced"] = (
                rolled["status"] == "read"
                and rolled["state"]["generation"] == 2
                and rolled["state"]["unresolved"] is True)
            proof["signing_key_on_disk"] = False
            if not all(proof[k] is True for k in (
                "prepare_fence_survived_restart",
                "unmodified_prepare_can_abort",
                "signed_unconfirmed_ack_survived_restart",
                "tampered_ack_cannot_release_fence",
                "correct_ack_can_confirm_after_restart",
                "postconfirm_allows_next_prepare",
                "restored_old_observer_db_vulnerability_reproduced",
            )):
                proof["errors"].append("durable observer subprocess evidence incomplete")
    except (OSError, ValueError, RuntimeError, TimeoutError, EOFError, KeyError):
        proof["errors"].append("durable observer subprocess experiment failed closed")
    return proof


def exit_status(proof: dict) -> int:
    required = (
        "prepare_fence_survived_restart",
        "unmodified_prepare_can_abort",
        "signed_unconfirmed_ack_survived_restart",
        "tampered_ack_cannot_release_fence",
        "correct_ack_can_confirm_after_restart",
        "postconfirm_allows_next_prepare",
        "restored_old_observer_db_vulnerability_reproduced",
    )
    guards = (
        "signing_key_on_disk", "independent_signer_custody_verified",
        "external_highwater_verified", "agent_dispatch_authorized",
        "model_egress_authorized", "used_real_repository",
    )
    return 0 if (
        all(proof.get(k) is True for k in required)
        and all(proof.get(k) is False for k in guards)
        and proof.get("provider_requests_forwarded") == 0
        and not proof.get("errors")
    ) else 2


if __name__ == "__main__":
    record = physical()
    print(json.dumps(record, indent=2, sort_keys=True))
    raise SystemExit(exit_status(record))
