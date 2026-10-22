#!/usr/bin/env python3
"""Process-restart SQLite synthetic lease evidence; no agent/provider operations.

One supervisor retains a random key *in RAM* and passes it only to forked,
short-lived fixture processes. The signing key is never persisted.
A deliberate old-DB restoration demonstrates the unresolved rollback risk.
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

from durable_lease_store import DurableSyntheticLeaseGate, UnsafeLeaseState

OWNER = "fixture-owner"
SESSION = "fixture-session"
MODEL = "kilo/kilo-auto/free"
WORKTREE = hashlib.sha256(b"throwaway-only-no-user-repository").hexdigest()
SCOPE = {"uid": os.getuid(), "owner": OWNER, "session": SESSION,
         "worktree_sha256": WORKTREE, "model_pin": MODEL}
VERIFY = {"peer_uid": os.getuid(), "expected_owner": OWNER,
          "expected_session": SESSION,
          "expected_worktree_sha256": WORKTREE, "expected_model_pin": MODEL}


def _child(path, key, cmd, conn, token, seq, create, start_event):
    """One ephemeral broker process for a single synthetic operation."""
    try:
        if start_event is not None and not start_event.wait(timeout=7):
            raise RuntimeError("fixture concurrency barrier expired")
        gate = DurableSyntheticLeaseGate(Path(path), key, create=create,
                                        now=lambda: 1000)
        if cmd == "mint":
            result = {"status": "minted", "token": gate.mint(**SCOPE)}
        elif cmd == "receipt":
            request = {"schema": 1, "op": "synthetic_receipt",
                       "token": token, "seq": seq}
            result = gate.check(request, **VERIFY)
        elif cmd == "revoke":
            result = {"status": "revoked" if gate.revoke(token) else "denied"}
        elif cmd == "rotate":
            result = {"status": "rotated", "epoch": gate.rotate_epoch()}
        elif cmd == "digest":
            result = {"status": "digest", "digest": gate.state_digest()}
        elif cmd == "missing":
            result = {"status": "unexpectedly_opened"}
        else:
            raise ValueError("unknown synthetic operation")
        conn.send(result)
    except Exception:
        conn.send({"status": "fail_closed"})
    finally:
        conn.close()


def physical() -> dict:
    result = {
        "schema": 1, "observed_host": socket.gethostname(),
        "mode": "synthetic_sqlite_restart_and_rollback_fixture",
        "restart_replay_denied": False,
        "restart_quota_enforced": False,
        "restart_revocation_denied": False,
        "restart_epoch_denied": False,
        "cross_process_concurrency_serialized": False,
        "db_missing_fail_closed": False,
        "key_not_persisted": False,
        "file_rollback_vulnerability_reproduced": False,
        "file_rollback_protection_verified": False,
        "independent_checkpoint_verified": False,
        "provider_requests_forwarded": 0,
        "agent_dispatch_authorized": False,
        "used_real_repository": False,
        "errors": [],
    }
    ctx = mp.get_context("fork")
    try:
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="og-restart-sqlite-",
                                         dir=cache) as root:
            directory = Path(root)
            os.chmod(directory, 0o700)
            db = directory / "lease-state.sqlite3"
            key = secrets.token_bytes(32)

            def step(cmd, *, token=None, seq=1, create=False,
                     start_event=None, timeout=12):
                receive, send = ctx.Pipe(duplex=False)
                process = ctx.Process(
                    target=_child,
                    args=(str(db), key, cmd, send, token, seq, create, start_event),
                    name="synthetic-lease-fixture",
                )
                process.daemon = False
                process.start()
                send.close()
                try:
                    if not receive.poll(timeout):
                        raise TimeoutError("fixture broker process unresponsive")
                    message = receive.recv()
                    process.join(timeout=3)
                    if process.is_alive() or process.exitcode != 0:
                        raise RuntimeError("fixture broker failed to exit")
                    return message
                finally:
                    receive.close()
                    if process.is_alive():
                        process.terminate()  # Only our own disposable child.
                        process.join(timeout=2)

            minted = step("mint", create=True)
            token = minted.get("token")
            if not isinstance(token, str):
                result["errors"].append("unable to mint fixture lease")
                return result
            first = step("receipt", token=token, seq=1)
            replay = step("receipt", token=token, seq=1)
            second = step("receipt", token=token, seq=2)
            third = step("receipt", token=token, seq=3)
            result["restart_replay_denied"] = (
                first.get("status") == "synthetic_receipt_only"
                and replay.get("status") == "denied")
            result["restart_quota_enforced"] = (
                second.get("status") == "synthetic_receipt_only"
                and third.get("status") == "denied"
                and second.get("receipt_number") == 2)

            token_revoke = step("mint").get("token")
            revocation = step("revoke", token=token_revoke)
            revoked_check = step("receipt", token=token_revoke)
            result["restart_revocation_denied"] = (
                revocation.get("status") == "revoked"
                and revoked_check.get("status") == "denied")

            token_epoch = step("mint").get("token")
            rotated = step("rotate")
            prior_epoch_check = step("receipt", token=token_epoch)
            result["restart_epoch_denied"] = (
                rotated.get("epoch") == 2
                and prior_epoch_check.get("status") == "denied")

            concurrent_token = step("mint").get("token")
            barrier = ctx.Event()
            pending = []
            for _ in range(8):
                a, b = ctx.Pipe(duplex=False)
                proc = ctx.Process(
                    target=_child,
                    args=(str(db), key, "receipt", b, concurrent_token,
                          1, False, barrier),
                    name="synthetic-lease-race",
                )
                proc.start()
                b.close()
                pending.append((proc, a))
            barrier.set()
            concurrent = []
            for proc, rx in pending:
                try:
                    if rx.poll(12):
                        concurrent.append(rx.recv().get("status"))
                finally:
                    rx.close()
                    proc.join(timeout=3)
                    if proc.is_alive():
                        proc.terminate()  # Only our own fixture child.
                        proc.join(timeout=2)
            result["cross_process_concurrency_serialized"] = (
                len(concurrent) == 8
                and concurrent.count("synthetic_receipt_only") == 1
                and concurrent.count("denied") == 7)

            # Reproduce a full valid DB rollback with a disposable snapshot.
            rollback_token = step("mint").get("token")
            snapshot = directory / "older-valid-image"
            shutil.copy2(db, snapshot)
            old_digest = step("digest").get("digest")
            redeem = step("receipt", token=rollback_token)
            new_digest = step("digest").get("digest")
            shutil.copy2(snapshot, db)
            restored_digest = step("digest").get("digest")
            resurrected = step("receipt", token=rollback_token)
            result["file_rollback_vulnerability_reproduced"] = (
                redeem.get("status") == "synthetic_receipt_only"
                and resurrected.get("status") == "synthetic_receipt_only"
                and old_digest != new_digest
                and restored_digest == old_digest)

            db.unlink()
            absent = step("missing")
            result["db_missing_fail_closed"] = (
                absent.get("status") == "fail_closed" and not db.exists())
            result["key_not_persisted"] = (
                key.hex().encode() not in snapshot.read_bytes()
                and not (directory / "signing-key").exists())
            if not all(result[k] for k in (
                "restart_replay_denied", "restart_quota_enforced",
                "restart_revocation_denied", "restart_epoch_denied",
                "cross_process_concurrency_serialized", "db_missing_fail_closed",
                "key_not_persisted", "file_rollback_vulnerability_reproduced",
            )):
                result["errors"].append("synthetic restart witness incomplete")
    except (OSError, ValueError, RuntimeError, TimeoutError, EOFError):
        result["errors"].append("synthetic process or storage check failed")
    return result


def exit_status(report: dict) -> int:
    checks = (
        "restart_replay_denied", "restart_quota_enforced",
        "restart_revocation_denied", "restart_epoch_denied",
        "cross_process_concurrency_serialized", "db_missing_fail_closed",
        "key_not_persisted", "file_rollback_vulnerability_reproduced",
    )
    return 0 if (
        all(report.get(k) is True for k in checks)
        and report.get("file_rollback_protection_verified") is False
        and report.get("independent_checkpoint_verified") is False
        and report.get("agent_dispatch_authorized") is False
        and report.get("provider_requests_forwarded") == 0
        and not report.get("errors")
    ) else 2


if __name__ == "__main__":
    report = physical()
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(exit_status(report))
