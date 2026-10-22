#!/usr/bin/env python3
"""Read-only observer on x1-370 against a disposable Lenovo fixture.

Observer Ed25519 private signing key never crosses SSH or touches disk.
The worker's HMAC key stays only in the remote process RAM. SSH transports
synthetic SQLite bytes and high-water observations. This does NOT prove
continual checkpoint publication or independent administrative custody.
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

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from observer_checkpoint import (
    ZERO, checkpoint_sha256, public_fingerprint,
    read_local_state_digest, sign_fixture, verify,
)

WORKER_SCRIPT = (
    "/home/scott/.local/share/herdr-omnigent-lenovo-pilot/work/"
    "observer_remote_worker_fixture.py"
)
REMOTE_HOST = "lenovo"


def physical(*, remote_host: str = REMOTE_HOST) -> dict:
    report = {
        "schema": 1, "mode": "two_node_ephemeral_observer_checkpoint",
        "observed_host": socket.gethostname(),
        "worker_host": "scott-Lenovo-ideapad-330S-15IKB",
        "signing_key_on_worker": False,
        "worker_secret_on_observer": False,
        "ephemeral_observer_key_held_separately": False,
        "newest_checkpoint_rejected_restored_db": False,
        "old_checkpoint_rejected_trusted_highwater": False,
        "old_checkpoint_with_old_floor_still_matches": False,
        "independent_long_term_custody_verified": False,
        "continuous_publication_verified": False,
        "rollback_resistance_verified": False,
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
        "used_real_repository": False,
        "errors": [],
    }
    if remote_host != REMOTE_HOST:
        report["errors"].append("unapproved fixture worker target")
        return report
    # Only a direct, bounded SSH process is started; never a persistent
    # daemon or real agent. All child/remote fixtures exit on stdin EOF.
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=6", remote_host,
        "python3", "-B", "-u", WORKER_SCRIPT,
    ]
    proc = None
    try:
        with tempfile.TemporaryDirectory(prefix="og-separate-observer-") as tmp:
            root = Path(tmp)
            os.chmod(root, 0o700)
            state = root / "worker-snapshot.sqlite3"
            observer_key = Ed25519PrivateKey.generate()
            public = observer_key.public_key()
            fingerprint = public_fingerprint(public)
            # No file stores the observer's private key.
            proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
            )
            if proc.stdin is None or proc.stdout is None:
                raise RuntimeError("SSH worker pipes unavailable")
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            try:
                def stage(command: str) -> dict:
                    proc.stdin.write(json.dumps({"op": command}) + "\n")
                    proc.stdin.flush()
                    if not selector.select(timeout=8):
                        raise TimeoutError("remote fixture did not answer")
                    line = proc.stdout.readline()
                    if not line or len(line) > 256 * 1024:
                        raise RuntimeError("empty/oversized worker reply")
                    message = json.loads(line)
                    if not isinstance(message, dict) or message.get("status") == "denied":
                        raise RuntimeError("remote fixture denied or returned bad data")
                    if command == "quit":
                        return message
                    encoded = message.get("sqlite_base64")
                    if not isinstance(encoded, str) or len(encoded) > 200 * 1024:
                        raise ValueError("unexpected remote fixture SQLite snapshot")
                    raw = base64.b64decode(encoded, validate=True)
                    if (len(raw) > 128 * 1024
                            or hashlib.sha256(raw).hexdigest()
                                != message.get("sqlite_sha256")):
                        raise ValueError("fixture SQLite transfer corrupted")
                    state.write_bytes(raw)
                    if (read_local_state_digest(state)
                            != message.get("state_sha256")):
                        raise ValueError("remote state digest not consistent")
                    return message

                first = stage("initial")
                cp1 = sign_fixture(
                    observer_key=observer_key, observer_id="x1-observer-fixture",
                    worker_id="lenovo-worker-fixture",
                    state_sha256=read_local_state_digest(state),
                    generation=1, previous_checkpoint_sha256=ZERO,
                    created_unix=1000)
                second = stage("redeem")
                cp2 = sign_fixture(
                    observer_key=observer_key, observer_id="x1-observer-fixture",
                    worker_id="lenovo-worker-fixture",
                    state_sha256=read_local_state_digest(state),
                    generation=2,
                    previous_checkpoint_sha256=checkpoint_sha256(cp1),
                    created_unix=1001)
                verifier = {
                    "state_path": state, "observer_public_key": public,
                    "trusted_observer_fingerprint": fingerprint,
                    "expected_observer_id": "x1-observer-fixture",
                    "expected_worker_id": "lenovo-worker-fixture",
                }
                latest = verify(
                    envelope=cp2, min_generation=2,
                    trusted_previous_sha256=checkpoint_sha256(cp1),
                    **verifier)
                restored = stage("restore")
                changed = verify(
                    envelope=cp2, min_generation=2,
                    trusted_previous_sha256=checkpoint_sha256(cp1),
                    **verifier)
                stale = verify(envelope=cp1, min_generation=2, **verifier)
                old_floor = verify(envelope=cp1, min_generation=1, **verifier)
                done = stage("quit")
                proc.stdin.close()
                proc.wait(timeout=5)
                report["ephemeral_observer_key_held_separately"] = (
                    first["status"] == "initial"
                    and second["status"] == "redeemed"
                    and restored["status"] == "restored_old_sqlite"
                    and done["status"] == "clean_exit"
                    and proc.returncode == 0)
                report["newest_checkpoint_rejected_restored_db"] = (
                    latest["signature_valid"] is True
                    and latest["checkpoint_matches_sqlite"] is True
                    and changed["signature_valid"] is True
                    and changed["checkpoint_matches_sqlite"] is False)
                report["old_checkpoint_rejected_trusted_highwater"] = (
                    stale["signature_valid"] is True
                    and stale["checkpoint_generation_acceptable"] is False)
                report["old_checkpoint_with_old_floor_still_matches"] = (
                    old_floor["signature_valid"] is True
                    and old_floor["checkpoint_matches_sqlite"] is True)
                if not all(report[k] is True for k in (
                    "ephemeral_observer_key_held_separately",
                    "newest_checkpoint_rejected_restored_db",
                    "old_checkpoint_rejected_trusted_highwater",
                    "old_checkpoint_with_old_floor_still_matches",
                )):
                    report["errors"].append("cross-host checkpoint witness incomplete")
            finally:
                selector.close()
    except (OSError, ValueError, RuntimeError, TimeoutError,
            subprocess.SubprocessError, json.JSONDecodeError):
        report["errors"].append("cross-host fixture aborted or failed closed")
    finally:
        if proc is not None and proc.poll() is None:
            # Only the SSH command created by this research probe.
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    return report


def exit_status(proof: dict) -> int:
    return 0 if (
        all(proof.get(k) is True for k in (
            "ephemeral_observer_key_held_separately",
            "newest_checkpoint_rejected_restored_db",
            "old_checkpoint_rejected_trusted_highwater",
            "old_checkpoint_with_old_floor_still_matches",
        ))
        and proof.get("signing_key_on_worker") is False
        and proof.get("worker_secret_on_observer") is False
        and proof.get("independent_long_term_custody_verified") is False
        and proof.get("continuous_publication_verified") is False
        and proof.get("rollback_resistance_verified") is False
        and proof.get("agent_dispatch_authorized") is False
        and proof.get("provider_requests_forwarded") == 0
        and not proof.get("errors")
    ) else 2


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--remote-host", default=REMOTE_HOST,
                     choices=[REMOTE_HOST])
    args = cli.parse_args()
    outcome = physical(remote_host=args.remote_host)
    print(json.dumps(outcome, indent=2, sort_keys=True))
    raise SystemExit(exit_status(outcome))
