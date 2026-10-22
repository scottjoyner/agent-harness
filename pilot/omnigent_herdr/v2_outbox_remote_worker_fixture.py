#!/usr/bin/env python3
"""Lenovo research-only V2 outbox worker for a single bounded SSH fixture.

The worker's V2 SQLite DB, HMAC secret and Ed25519 worker signer are
ephemeral and deleted on exit. The observer's ephemeral public key is
introduced over the SAME SSH session; this is NOT independent trust-root
custody, source attestation or approval to run any agent/model/provider.
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

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from observer_checkpoint import ZERO, public_fingerprint
from v2_outbox_publication import (
    worker_publish, worker_sign_delivery,
)

WORKTREE = hashlib.sha256(b"disposable-v2-physical-outbox").hexdigest()
OPERATION = "a" * 32
OBSERVER_ID = "x1-v2-observer"
WORKER_ID = "lenovo-v2-worker"
MAX_COMMAND = 16 * 1024


def _emit(sink, *, status: str, **fields) -> None:
    record = {
        "schema": 1, "status": status,
        "agent_dispatch_authorized": False,
        "model_egress_authorized": False,
        "provider_requests_forwarded": 0,
        **fields,
    }
    sink.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    sink.flush()


def run(stdin=sys.stdin, stdout=sys.stdout) -> None:
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix="og-v2-outbox-physical-", dir=cache) as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        db = root / "v2-outbox.sqlite3"
        hmac_key = secrets.token_bytes(32)
        signing_key = Ed25519PrivateKey.generate()
        key = signing_key.public_key()
        worker_pin = public_fingerprint(key)
        worker_key_encoded = base64.b64encode(key.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )).decode("ascii")
        gate = AtomicSyntheticLeaseOutbox(
            db, hmac_key, now=lambda: 1000, create=True)
        token = gate.mint(
            uid=os.getuid(), owner="synthetic-owner",
            session="synthetic-session",
            worktree_sha256=WORKTREE,
            model_pin="kilo/kilo-auto/free")
        request = {
            "schema": 1, "op": "synthetic_receipt",
            "token": token, "seq": 1,
        }
        expected = {
            "peer_uid": os.getuid(),
            "expected_owner": "synthetic-owner",
            "expected_session": "synthetic-session",
            "expected_worktree_sha256": WORKTREE,
            "expected_model_pin": "kilo/kilo-auto/free",
        }
        observer_key = None
        observer_pin = None
        original_publication = None
        expected_head = None
        stage = 0
        for line in stdin:
            try:
                if len(line) > MAX_COMMAND:
                    raise ValueError("oversized fixture command")
                command = json.loads(line)
                if not isinstance(command, dict):
                    raise ValueError("malformed command")
                op = command.get("op")
                if op == "init" and stage == 0 and set(command) == {
                    "op", "observer_public_key", "observer_fingerprint",
                }:
                    obs_bytes = base64.b64decode(
                        command["observer_public_key"], validate=True)
                    if len(obs_bytes) != 32:
                        raise ValueError("invalid observer public key length")
                    observer_key = Ed25519PublicKey.from_public_bytes(obs_bytes)
                    observer_pin = public_fingerprint(observer_key)
                    if (observer_pin != command["observer_fingerprint"]
                            or observer_pin == worker_pin):
                        raise ValueError("invalid/overlapping research signer")
                    stage = 1
                    _emit(stdout, status="initial", worker_id=WORKER_ID,
                          observer_id=OBSERVER_ID,
                          worker_public_key=worker_key_encoded,
                          worker_fingerprint=worker_pin,
                          operation_id=OPERATION,
                          before_state_sha256=gate.state_digest())
                elif op == "commit" and stage == 1 and set(command) == {"op"}:
                    accepted = gate.check(
                        request, operation_id=OPERATION, **expected)
                    if accepted.get("status") != "synthetic_receipt_only":
                        raise ValueError("synthetic debit denied")
                    expected_head = (
                        accepted["atomic_outbox_record"],
                        accepted["atomic_outbox_sha256"])
                    exported = gate.export_head_event(
                        trusted_anchor=expected_head)
                    original_publication = worker_publish(
                        signer=signing_key,
                        worker_id=WORKER_ID,
                        observer_id=OBSERVER_ID,
                        observer_fingerprint=observer_pin,
                        exported_head=exported)
                    stage = 2
                    _emit(stdout, status="awaiting_observer_ACK",
                          signed_publication=original_publication)
                elif op == "ack" and stage == 2 and set(command) == {
                    "op", "signed_ack",
                }:
                    # The exact worker-local V2 outbox state must still
                    # match before signing delivery of any observer ACK.
                    current = gate.export_head_event(
                        trusted_anchor=expected_head)
                    if (current["lease_state_sha256"]
                            != original_publication["payload"][
                                "lease_state_sha256"]):
                        raise ValueError("worker V2 state changed")
                    delivery = worker_sign_delivery(
                        signer=signing_key,
                        publication=original_publication,
                        observer_ack=command["signed_ack"],
                        observer_public_key=observer_key,
                        trusted_observer_fingerprint=observer_pin,
                        expected_generation=2,
                        previous_checkpoint_sha256=ZERO)
                    stage = 3
                    _emit(stdout, status="synthetic_ACK_delivery_only",
                          signed_delivery=delivery)
                elif op == "quit" and stage == 3 and set(command) == {"op"}:
                    _emit(stdout, status="clean_exit")
                    return
                else:
                    raise ValueError("out-of-order command")
            except (ValueError, TypeError, RuntimeError, KeyError,
                    binascii.Error, OSError):
                _emit(stdout, status="denied")
                return


if __name__ == "__main__":
    run()
