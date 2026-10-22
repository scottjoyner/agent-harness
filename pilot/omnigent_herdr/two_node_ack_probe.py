#!/usr/bin/env python3
"""Cross-node synthetic two-phase ACK fixture: x1 observer, Lenovo worker.

Runs one ephemeral, bounded SSH worker. No provider traffic, agent launch,
production DB, user source or permanent signer. Per-run Ed25519 key lives
only in x1 observer process RAM; Lenovo owns its own synthetic HMAC key.
The public key is bootstrapped *inside this test* and is NOT an independently
pinned trust root. Source authority and durable publication remain unproven.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import selectors
import socket
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from observer_ack_protocol import EphemeralObserver, verify_ack
from observer_checkpoint import (
    _json_bytes, checkpoint_sha256, public_fingerprint,
    read_local_state_digest, verify as verify_checkpoint,
)
from worker_acceptance_receipt import verify as verify_worker_receipt

REMOTE_HOST = "lenovo"
REMOTE_WORKER = (
    "/home/scott/.local/share/herdr-omnigent-lenovo-pilot/work/"
    "ack_remote_worker_fixture.py"
)
SCENARIOS = (
    "success", "missing_ack", "tampered_ack",
    "tampered_worker_commit", "tampered_worker_delivery",
)


def physical(*, scenario: str = "success", remote_host: str = REMOTE_HOST) -> dict:
    report = {
        "schema": 1,
        "observed_host": socket.gethostname(),
        "remote_worker": "scott-Lenovo-ideapad-330S-15IKB",
        "mode": "two_node_synthetic_observer_ack_fixture",
        "scenario": scenario,
        "signed_prepare_verified": False,
        "worker_committed_synthetic_receipt": False,
        "observer_validated_postcommit_snapshot": False,
        "worker_key_distinct_from_observer": False,
        "worker_commit_receipt_verified": False,
        "worker_delivery_receipt_verified": False,
        "tampered_worker_receipt_denied": False,
        "signed_checkpoint_verified": False,
        "signed_ack_accepted": False,
        "missing_or_tampered_ack_denied": False,
        "observer_fenced_after_failed_delivery": False,
        "clean_worker_exit": False,
        "observer_private_key_sent_to_worker": False,
        "worker_hmac_key_sent_to_observer": False,
        "worker_signing_key_sent_to_observer": False,
        "worker_trust_root_independently_pinned": False,
        "worker_receipt_durability_verified": False,
        "independent_key_custody_verified": False,
        "durable_publication_verified": False,
        "rollback_resistance_verified": False,
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
        "used_real_repository": False,
        "errors": [],
    }
    if remote_host != REMOTE_HOST or scenario not in SCENARIOS:
        report["errors"].append("target or scenario not authorized")
        return report
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=6", remote_host,
        "python3", "-B", "-u", REMOTE_WORKER,
    ]
    proc = None
    try:
        with tempfile.TemporaryDirectory(prefix="og-ack-observer-") as tmp:
            temp = Path(tmp)
            os.chmod(temp, 0o700)
            snapshot = temp / "remote-synthetic.sqlite3"
            signer = Ed25519PrivateKey.generate()
            proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
            )
            if proc.stdin is None or proc.stdout is None:
                raise RuntimeError("worker fixture SSH channels unavailable")
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            try:
                def stage(packet: dict) -> dict:
                    if proc.poll() is not None:
                        raise RuntimeError("worker exited early")
                    encoded = json.dumps(packet, sort_keys=True, separators=(",", ":"))
                    if len(encoded) > 16 * 1024:
                        raise ValueError("oversized synthetic command")
                    proc.stdin.write(encoded + "\n")
                    proc.stdin.flush()
                    if not selector.select(timeout=8):
                        raise TimeoutError("observer response deadline")
                    line = proc.stdout.readline()
                    if not line or len(line) > 256 * 1024:
                        raise RuntimeError("missing or oversized worker evidence")
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("bad worker reply")
                    return record

                def validate_snapshot(message: dict) -> str:
                    raw_data = message.get("sqlite_base64")
                    if not isinstance(raw_data, str) or len(raw_data) > 200 * 1024:
                        raise ValueError("oversized worker DB image")
                    raw = base64.b64decode(raw_data, validate=True)
                    if (len(raw) > 128 * 1024
                            or hashlib.sha256(raw).hexdigest()
                            != message.get("sqlite_sha256")):
                        raise ValueError("worker transfer digest mismatch")
                    snapshot.write_bytes(raw)
                    state = read_local_state_digest(snapshot)
                    if state != message.get("state_sha256"):
                        raise ValueError("worker state digest mismatch")
                    return state

                initial = stage({"op": "init"})
                if initial.get("status") != "initial":
                    raise RuntimeError("fixture setup denied")
                before = validate_snapshot(initial)
                worker_key_bytes = base64.b64decode(
                    initial["worker_public_key"], validate=True)
                if len(worker_key_bytes) != 32:
                    raise ValueError("worker key length")
                worker_key = Ed25519PublicKey.from_public_bytes(
                    worker_key_bytes)
                worker_pin = public_fingerprint(worker_key)
                if worker_pin != initial["worker_public_fingerprint"]:
                    raise ValueError("worker key and advertised pin mismatch")
                report["worker_key_distinct_from_observer"] = (
                    worker_pin != public_fingerprint(signer.public_key()))
                if not report["worker_key_distinct_from_observer"]:
                    raise ValueError("reused observer signing role")
                operation_id = initial["operation_id"]
                request_sha = initial["request_sha256"]
                observer = EphemeralObserver(
                    signer=signer, observer_id="x1-ack-observer",
                    worker_id="lenovo-ack-worker", initial_state=before)
                prepared = observer.prepare(
                    operation_id=operation_id,
                    request_sha256=request_sha, previous_state=before)
                public = signer.public_key().public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw)
                worker = stage({
                    "op": "prepare", "record": prepared,
                    "public_key": base64.b64encode(public).decode("ascii"),
                    "fingerprint": observer.fingerprint,
                })
                if worker.get("status") != "pending_observer_ACK":
                    raise RuntimeError("worker did not commit synthetic receipt")
                after = validate_snapshot(worker)
                report["signed_prepare_verified"] = True
                report["worker_committed_synthetic_receipt"] = after != before
                report["observer_validated_postcommit_snapshot"] = after != before
                committed_fields = {
                    "worker_id": "lenovo-ack-worker",
                    "observer_id": "x1-ack-observer",
                    "operation_id": operation_id,
                    "request_sha256": request_sha,
                    "prepare_sha256": hashlib.sha256(
                        _json_bytes(prepared)).hexdigest(),
                    "before_state_sha256": before,
                    "after_state_sha256": after,
                    "generation": 2,
                    "sequence": 1,
                    "receipt_number": 1,
                    "agent_dispatch_authorized": False,
                    "provider_requests_forwarded": 0,
                }
                worker_commit = worker.get("worker_commit_receipt")
                if scenario == "tampered_worker_commit":
                    worker_commit = {
                        **worker_commit,
                        "payload": {
                            **worker_commit["payload"],
                            "after_state_sha256": "f" * 64,
                        },
                    }
                report["worker_commit_receipt_verified"] = (
                    verify_worker_receipt(
                        envelope=worker_commit, public_key=worker_key,
                        trusted_fingerprint=worker_pin,
                        expected_kind="synthetic_worker_commit",
                        expected=committed_fields,
                        observer_fingerprint=observer.fingerprint)
                )
                if scenario == "tampered_worker_commit":
                    report["tampered_worker_receipt_denied"] = (
                        not report["worker_commit_receipt_verified"])
                    report["observer_fenced_after_failed_delivery"] = (
                        observer.pending is not None)
                    # The synthetic debit may have committed; the observer
                    # must not create a checkpoint or release the fence.
                    proc.stdin.close()
                    proc.wait(timeout=6)
                    report["clean_worker_exit"] = proc.returncode == 0
                    return report
                if not report["worker_commit_receipt_verified"]:
                    raise RuntimeError("unsigned, stale or modified worker commit")
                signed_ack = observer.acknowledge(
                    prepared=prepared, after_state=after,
                    independently_observed_state=after)
                checkpoint = observer.current
                verified_checkpoint = verify_checkpoint(
                    state_path=snapshot,
                    envelope=checkpoint,
                    observer_public_key=signer.public_key(),
                    trusted_observer_fingerprint=observer.fingerprint,
                    expected_observer_id="x1-ack-observer",
                    expected_worker_id="lenovo-ack-worker",
                    min_generation=2,
                    trusted_previous_sha256=prepared["payload"][
                        "previous_checkpoint_sha256"],
                )
                report["signed_checkpoint_verified"] = (
                    signed_ack["payload"]["checkpoint_sha256"]
                    == checkpoint_sha256(checkpoint)
                    and verified_checkpoint["signature_valid"] is True
                    and verified_checkpoint["checkpoint_matches_sqlite"] is True
                    and verified_checkpoint["checkpoint_generation_acceptable"] is True)
                if scenario == "missing_ack":
                    offered_ack = None
                elif scenario == "tampered_ack":
                    offered_ack = {
                        **signed_ack,
                        "payload": {**signed_ack["payload"],
                                    "after_state_sha256": "f" * 64},
                    }
                else:
                    offered_ack = signed_ack
                ack_reply = stage({
                    "op": "ack",
                    "record": offered_ack,
                    "checkpoint": checkpoint,
                })
                if scenario in ("success", "tampered_worker_delivery"):
                    worker_delivery = ack_reply.get("worker_delivery_receipt")
                    if scenario == "tampered_worker_delivery" and isinstance(
                            worker_delivery, dict):
                        worker_delivery = {
                            **worker_delivery,
                            "payload": {
                                **worker_delivery["payload"],
                                "observer_ack_sha256": "f" * 64,
                            },
                        }
                    delivery_fields = {
                        **committed_fields,
                        "observer_ack_sha256": hashlib.sha256(
                            _json_bytes(signed_ack)).hexdigest(),
                        "observer_checkpoint_sha256": checkpoint_sha256(
                            checkpoint),
                    }
                    report["worker_delivery_receipt_verified"] = (
                        ack_reply.get("status") == "synthetic_receipt_only"
                        and ack_reply.get("observer_acknowledged") is True
                        and ack_reply.get("agent_dispatch_authorized") is False
                        and ack_reply.get("provider_requests_forwarded") == 0
                        and verify_worker_receipt(
                            envelope=worker_delivery,
                            public_key=worker_key,
                            trusted_fingerprint=worker_pin,
                            expected_kind="synthetic_worker_delivery",
                            expected=delivery_fields,
                            observer_fingerprint=observer.fingerprint)
                    )
                    if scenario == "tampered_worker_delivery":
                        report["tampered_worker_receipt_denied"] = (
                            report["worker_delivery_receipt_verified"] is False)
                        report["observer_fenced_after_failed_delivery"] = (
                            observer.fenced)
                        proc.stdin.close()
                        proc.wait(timeout=6)
                        report["clean_worker_exit"] = proc.returncode == 0
                        return report
                    report["signed_ack_accepted"] = (
                        report["worker_delivery_receipt_verified"])
                    if report["signed_ack_accepted"]:
                        observer.confirm_ack_delivery(signed_ack)
                        last = stage({"op": "quit"})
                        report["clean_worker_exit"] = (
                            last.get("status") == "clean_exit")
                        proc.stdin.close()
                        proc.wait(timeout=6)
                        report["clean_worker_exit"] &= proc.returncode == 0
                else:
                    report["missing_or_tampered_ack_denied"] = (
                        ack_reply.get("status") == "denied"
                        and ack_reply.get("observer_acknowledged") is False
                        and ack_reply.get("agent_dispatch_authorized") is False
                        and ack_reply.get("provider_requests_forwarded") == 0)
                    report["observer_fenced_after_failed_delivery"] = observer.fenced
                    proc.stdin.close()
                    proc.wait(timeout=6)
                    report["clean_worker_exit"] = proc.returncode == 0
            finally:
                selector.close()
    except (OSError, ValueError, RuntimeError, TypeError, TimeoutError,
            json.JSONDecodeError, subprocess.SubprocessError):
        report["errors"].append("two-node synthetic ACK fixture denied")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()  # Only the SSH command spawned by this fixture.
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    return report


def exit_status(record: dict) -> int:
    base = all(record.get(k) is True for k in (
        "signed_prepare_verified", "worker_committed_synthetic_receipt",
        "observer_validated_postcommit_snapshot", "clean_worker_exit",
        "worker_key_distinct_from_observer",
    ))
    safe = (
        record.get("observer_private_key_sent_to_worker") is False
        and record.get("worker_hmac_key_sent_to_observer") is False
        and record.get("worker_signing_key_sent_to_observer") is False
        and record.get("worker_trust_root_independently_pinned") is False
        and record.get("worker_receipt_durability_verified") is False
        and record.get("independent_key_custody_verified") is False
        and record.get("durable_publication_verified") is False
        and record.get("rollback_resistance_verified") is False
        and record.get("agent_dispatch_authorized") is False
        and record.get("provider_requests_forwarded") == 0
        and record.get("used_real_repository") is False
        and not record.get("errors")
    )
    scenario = record.get("scenario")
    if scenario == "success":
        outcome = (
            record.get("signed_checkpoint_verified") is True
            and record.get("worker_commit_receipt_verified") is True
            and record.get("worker_delivery_receipt_verified") is True
            and record.get("signed_ack_accepted") is True
            and record.get("missing_or_tampered_ack_denied") is False
            and record.get("tampered_worker_receipt_denied") is False)
    elif scenario in ("missing_ack", "tampered_ack"):
        outcome = (
            record.get("signed_checkpoint_verified") is True
            and record.get("worker_commit_receipt_verified") is True
            and record.get("worker_delivery_receipt_verified") is False
            and record.get("signed_ack_accepted") is False
            and record.get("missing_or_tampered_ack_denied") is True
            and record.get("tampered_worker_receipt_denied") is False
            and record.get("observer_fenced_after_failed_delivery") is True)
    elif scenario == "tampered_worker_commit":
        outcome = (
            record.get("signed_checkpoint_verified") is False
            and record.get("worker_commit_receipt_verified") is False
            and record.get("worker_delivery_receipt_verified") is False
            and record.get("signed_ack_accepted") is False
            and record.get("tampered_worker_receipt_denied") is True
            and record.get("observer_fenced_after_failed_delivery") is True)
    elif scenario == "tampered_worker_delivery":
        outcome = (
            record.get("signed_checkpoint_verified") is True
            and record.get("worker_commit_receipt_verified") is True
            and record.get("worker_delivery_receipt_verified") is False
            and record.get("signed_ack_accepted") is False
            and record.get("tampered_worker_receipt_denied") is True
            and record.get("observer_fenced_after_failed_delivery") is True)
    else:
        outcome = False
    return 0 if base and safe and outcome else 2


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--scenario", choices=SCENARIOS, default="success")
    args = cli.parse_args()
    value = physical(scenario=args.scenario)
    print(json.dumps(value, sort_keys=True, indent=2))
    raise SystemExit(exit_status(value))
