#!/usr/bin/env python3
"""Lenovo-only fixture for two-node signed synthetic receipt ACK.

No model, shell dispatch, provider credential, real repository or service.
The SQLite worker key exists only in this process RAM. The observer's
private key must never be copied here. Temporary DB disappears on exit.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from durable_lease_store import DurableSyntheticLeaseGate
from observer_ack_protocol import (
    request_digest, verify_prepare, verify_ack,
)
from observer_checkpoint import (
    checkpoint_sha256, read_local_state_digest, verify as verify_checkpoint,
)

WORKTREE = hashlib.sha256(b"synthetic-cross-node-ACK-no-real-repo").hexdigest()


def _reply(data: dict, sink) -> None:
    # Force all code paths (including setup and failure) to be non-admitting.
    payload = dict(data)
    payload.update({
        "agent_dispatch_authorized": False,
        "model_egress_authorized": False,
        "provider_requests_forwarded": 0,
    })
    sink.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    sink.flush()


def _snapshot(db: Path) -> dict:
    contents = db.read_bytes()
    if len(contents) > 128 * 1024:
        raise RuntimeError("synthetic SQLite image unexpectedly large")
    return {
        "sqlite_base64": base64.b64encode(contents).decode("ascii"),
        "sqlite_sha256": hashlib.sha256(contents).hexdigest(),
        "state_sha256": read_local_state_digest(db),
    }


def run(source=sys.stdin, sink=sys.stdout) -> None:
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="og-ack-worker-", dir=cache) as root:
        directory = Path(root)
        os.chmod(directory, 0o700)
        db = directory / "synthetic.sqlite3"
        worker = DurableSyntheticLeaseGate(
            db, secrets.token_bytes(32), now=lambda: 1000, create=True)
        scope = {
            "uid": os.getuid(), "owner": "synthetic-owner",
            "session": "synthetic-session",
            "worktree_sha256": WORKTREE,
            "model_pin": "kilo/kilo-auto/free",
        }
        expected = {
            "peer_uid": os.getuid(),
            "expected_owner": scope["owner"],
            "expected_session": scope["session"],
            "expected_worktree_sha256": WORKTREE,
            "expected_model_pin": scope["model_pin"],
        }
        token = worker.mint(**scope)
        request = {"schema": 1, "op": "synthetic_receipt",
                   "token": token, "seq": 1}
        operation_id = secrets.token_hex(16)
        request_sha256 = request_digest(request)
        prepare = None
        after = None
        before = worker.state_digest()
        key = None
        fingerprint = None
        stage = 0
        for line in source:
            try:
                if len(line) > 16 * 1024:
                    raise ValueError("input exceeds fixture limit")
                packet = json.loads(line)
                if not isinstance(packet, dict):
                    raise ValueError("invalid fixture request")
                op = packet.get("op")
                if op == "init" and set(packet) == {"op"} and stage == 0:
                    stage = 1
                    _reply({
                        "status": "initial",
                        "operation_id": operation_id,
                        "request_sha256": request_sha256,
                        "worker_id": "lenovo-ack-worker",
                        **_snapshot(db),
                    }, sink)
                elif op == "prepare" and set(packet) == {
                    "op", "record", "public_key", "fingerprint"
                } and stage == 1:
                    raw = base64.b64decode(packet["public_key"], validate=True)
                    if len(raw) != 32:
                        raise ValueError("observer public key length")
                    key = Ed25519PublicKey.from_public_bytes(raw)
                    fingerprint = packet["fingerprint"]
                    prepare = packet["record"]
                    if not verify_prepare(
                            prepare, public_key=key,
                            fingerprint=fingerprint,
                            expected={
                                "observer_id": "x1-ack-observer",
                                "worker_id": "lenovo-ack-worker",
                                "operation_id": operation_id,
                                "request_sha256": request_sha256,
                                "before_state_sha256": before,
                                "generation": 2,
                            }):
                        raise ValueError("invalid signed prepare")
                    accepted = worker.check(request, **expected)
                    if accepted.get("status") != "synthetic_receipt_only":
                        raise RuntimeError("no synthetic receipt was committed")
                    after = worker.state_digest()
                    stage = 2
                    _reply({"status": "pending_observer_ACK", **_snapshot(db)}, sink)
                elif op == "ack" and set(packet) == {
                    "op", "record", "checkpoint"
                } and stage == 2:
                    ack = packet["record"]
                    checkpoint = packet["checkpoint"]
                    observation = verify_checkpoint(
                        state_path=db, envelope=checkpoint,
                        observer_public_key=key,
                        trusted_observer_fingerprint=fingerprint,
                        expected_observer_id="x1-ack-observer",
                        expected_worker_id="lenovo-ack-worker",
                        min_generation=2,
                        trusted_previous_sha256=prepare["payload"][
                            "previous_checkpoint_sha256"],
                    )
                    was_confirmed = (
                        observation["signature_valid"] is True
                        and observation["checkpoint_matches_sqlite"] is True
                        and observation["checkpoint_generation_acceptable"] is True
                        and verify_ack(
                        ack, public_key=key, fingerprint=fingerprint,
                        expected={
                            "observer_id": "x1-ack-observer",
                            "worker_id": "lenovo-ack-worker",
                            "operation_id": operation_id,
                            "request_sha256": request_sha256,
                            "before_state_sha256": before,
                            "after_state_sha256": after,
                            "generation": 2,
                            "previous_checkpoint_sha256":
                                prepare["payload"]["previous_checkpoint_sha256"],
                            "checkpoint_sha256":
                                checkpoint_sha256(checkpoint),
                        }))
                    # Physical key custody, publication and independently
                    # trusted freshness remain unverified.
                    if not was_confirmed:
                        raise ValueError("missing or invalid signed ACK")
                    stage = 3
                    _reply({
                        "status": "synthetic_receipt_only",
                        "observer_acknowledged": True,
                        "agent_dispatch_authorized": False,
                        "model_egress_authorized": False,
                        "provider_requests_forwarded": 0,
                    }, sink)
                elif op == "quit" and set(packet) == {"op"} and stage == 3:
                    _reply({"status": "clean_exit"}, sink)
                    return
                else:
                    raise ValueError("fixture sequence not authorized")
            except (ValueError, RuntimeError, TypeError, KeyError, IndexError,
                    binascii.Error):
                _reply({
                    "status": "denied",
                    "observer_acknowledged": False,
                    "agent_dispatch_authorized": False,
                    "model_egress_authorized": False,
                    "provider_requests_forwarded": 0,
                }, sink)
                return


if __name__ == "__main__":
    run()
