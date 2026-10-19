#!/usr/bin/env python3
"""Ephemeral Lenovo worker for cross-node checkpoint experiment.

One synthetic worker key is generated in RAM. No LLM, real repositories,
provider credentials, or observer signing keys. JSON-line/stdin only.
All SQLite fixture files are temporary and removed on process exit.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from durable_lease_store import DurableSyntheticLeaseGate
from observer_checkpoint import read_local_state_digest


WORKTREE = hashlib.sha256(b"two-node-remote-synthetic-only").hexdigest()


def _snapshot(db: Path) -> dict:
    raw = db.read_bytes()
    if len(raw) > 128 * 1024:
        raise RuntimeError("fixture database size exceeded")
    return {
        "sqlite_base64": base64.b64encode(raw).decode("ascii"),
        "sqlite_sha256": hashlib.sha256(raw).hexdigest(),
        "state_sha256": read_local_state_digest(db),
    }


def run(stdin=sys.stdin, stdout=sys.stdout) -> None:
    # Nothing outside one private, temporary fixture root is written.
    with tempfile.TemporaryDirectory(prefix="og-remote-observer-",
                                     dir=Path.home() / ".cache") as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        db = root / "lease-state.sqlite3"
        key = os.urandom(32)
        gate = DurableSyntheticLeaseGate(
            db, key, create=True, now=lambda: 1000)
        claims = {
            "uid": os.getuid(), "owner": "fixture-owner",
            "session": "fixture-session", "worktree_sha256": WORKTREE,
            "model_pin": "kilo/kilo-auto/free",
        }
        match = {
            "peer_uid": os.getuid(), "expected_owner": "fixture-owner",
            "expected_session": "fixture-session",
            "expected_worktree_sha256": WORKTREE,
            "expected_model_pin": "kilo/kilo-auto/free",
        }
        token = gate.mint(**claims)
        snapshot = root / "older-valid-image"
        shutil.copy2(db, snapshot)
        stage = 0
        for raw in stdin:
            try:
                if len(raw) > 1024:
                    raise ValueError("oversized fixture command")
                msg = json.loads(raw)
                if not isinstance(msg, dict) or set(msg) != {"op"}:
                    raise ValueError("invalid fixture command")
                op = msg["op"]
                if op == "initial" and stage == 0:
                    stage = 1
                    reply = {"status": "initial", **_snapshot(db)}
                elif op == "redeem" and stage == 1:
                    outcome = gate.check(
                        {"schema": 1, "op": "synthetic_receipt",
                         "token": token, "seq": 1}, **match)
                    if outcome.get("status") != "synthetic_receipt_only":
                        raise RuntimeError("synthetic receipt unexpectedly denied")
                    stage = 2
                    reply = {"status": "redeemed", **_snapshot(db)}
                elif op == "restore" and stage == 2:
                    shutil.copy2(snapshot, db)
                    stage = 3
                    reply = {"status": "restored_old_sqlite", **_snapshot(db)}
                elif op == "quit" and stage == 3:
                    reply = {"status": "clean_exit"}
                    stdout.write(json.dumps(reply, sort_keys=True) + "\n")
                    stdout.flush()
                    break
                else:
                    raise ValueError("out-of-order fixture command")
                stdout.write(json.dumps(reply, sort_keys=True) + "\n")
                stdout.flush()
            except (ValueError, OSError, RuntimeError, KeyError):
                stdout.write(json.dumps({"status": "denied"}) + "\n")
                stdout.flush()
                break


if __name__ == "__main__":
    run()
