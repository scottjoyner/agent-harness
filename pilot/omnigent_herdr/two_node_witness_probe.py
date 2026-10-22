#!/usr/bin/env python3
"""x1 observer cooperative witness log vs disposable Lenovo SQLite worker.

Only synthetic SQLite fixture bytes cross an existing strict-host-key SSH
connection; the Ed25519 signing key remains in x1 RAM. The local witness
log is disposable; neither its file nor latest-anchor variable has
independent administrative custody. No model calls, runtime dispatch,
user repositories, or production changes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import selectors
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from checkpoint_witness_log import CheckpointWitnessLog, UnsafeWitness
from observer_checkpoint import ZERO, checkpoint_sha256, read_local_state_digest, sign_fixture

REMOTE_HOST = "lenovo"
WORKER_SCRIPT = (
    "/home/scott/.local/share/herdr-omnigent-lenovo-pilot/work/"
    "observer_remote_worker_fixture.py"
)


def physical(*, remote_host: str = REMOTE_HOST) -> dict:
    report = {
        "schema": 1, "mode": "two_node_cooperative_witness_log",
        "observed_host": socket.gethostname(),
        "worker_host": "scott-Lenovo-ideapad-330S-15IKB",
        "two_signed_checkpoints_published": False,
        "worker_old_state_rejected_against_newer_head": False,
        "truncated_witness_rejected_with_newer_anchor": False,
        "stale_writer_rejected": False,
        "old_log_and_old_anchor_still_match": False,
        "remote_fixture_exited_cleanly": False,
        "signer_key_sent_to_worker": False,
        "worker_key_sent_to_observer": False,
        "independent_anchor_custody_verified": False,
        "source_attestation_verified": False,
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
        "used_real_repository": False,
        "errors": [],
    }
    if remote_host != REMOTE_HOST:
        report["errors"].append("unapproved remote fixture node")
        return report
    proc = None
    try:
        with tempfile.TemporaryDirectory(prefix="og-witness-remote-") as temp:
            root = Path(temp)
            os.chmod(root, 0o700)
            snapshot = root / "remote-worker.sqlite3"
            log_path = root / "witness.jsonl"
            previous_image = root / "previous-witness"
            signer = Ed25519PrivateKey.generate()
            observer = CheckpointWitnessLog(
                log_path, signer=signer, observer_public_key=signer.public_key(),
                observer_id="x1-witness-fixture",
                worker_id="lenovo-witness-worker")
            reader = CheckpointWitnessLog(
                log_path, observer_public_key=signer.public_key(),
                observer_id="x1-witness-fixture",
                worker_id="lenovo-witness-worker")
            argv = [
                "ssh", "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=6",
                remote_host, "python3", "-B", "-u", WORKER_SCRIPT,
            ]
            proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1)
            if proc.stdin is None or proc.stdout is None:
                raise RuntimeError("worker fixture channels unavailable")
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            try:
                def stage(op: str) -> tuple[dict, str | None]:
                    if proc.poll() is not None:
                        raise RuntimeError("remote fixture terminated prematurely")
                    proc.stdin.write(json.dumps({"op": op}) + "\n")
                    proc.stdin.flush()
                    if not selector.select(timeout=8):
                        raise TimeoutError("worker snapshot deadline")
                    data = proc.stdout.readline()
                    if not data or len(data) > 256 * 1024:
                        raise ValueError("empty or oversized fixture record")
                    message = json.loads(data)
                    if not isinstance(message, dict) or message.get("status") == "denied":
                        raise RuntimeError("remote fixture denied")
                    if op == "quit":
                        return message, None
                    encoded = message.get("sqlite_base64")
                    if not isinstance(encoded, str) or len(encoded) > 200 * 1024:
                        raise ValueError("worker image not bounded")
                    raw = base64.b64decode(encoded, validate=True)
                    if (len(raw) > 128 * 1024
                            or hashlib.sha256(raw).hexdigest()
                            != message.get("sqlite_sha256")):
                        raise ValueError("worker transfer digest mismatch")
                    snapshot.write_bytes(raw)
                    digest = read_local_state_digest(snapshot)
                    if digest != message.get("state_sha256"):
                        raise ValueError("worker snapshot state mismatch")
                    return message, digest

                initial, before = stage("initial")
                cp1 = sign_fixture(
                    observer_key=signer,
                    observer_id="x1-witness-fixture",
                    worker_id="lenovo-witness-worker",
                    state_sha256=before, generation=1,
                    previous_checkpoint_sha256=ZERO, created_unix=1000)
                anchor1 = observer.publish(cp1, trusted_anchor=None, genesis=True)
                shutil.copy2(log_path, previous_image)
                redeemed, after = stage("redeem")
                cp2 = sign_fixture(
                    observer_key=signer,
                    observer_id="x1-witness-fixture",
                    worker_id="lenovo-witness-worker",
                    state_sha256=after, generation=2,
                    previous_checkpoint_sha256=checkpoint_sha256(cp1),
                    created_unix=1001)
                anchor2 = observer.publish(cp2, trusted_anchor=anchor1)
                newest = reader.inspect(trusted_anchor=anchor2)
                report["two_signed_checkpoints_published"] = (
                    initial.get("status") == "initial"
                    and redeemed.get("status") == "redeemed"
                    and after != before
                    and newest["latest_generation"] == 2
                    and newest["latest_state_sha256"] == after
                    and newest["agent_dispatch_authorized"] is False)
                restored, old = stage("restore")
                report["worker_old_state_rejected_against_newer_head"] = (
                    restored.get("status") == "restored_old_sqlite"
                    and old == before
                    and old != newest["latest_state_sha256"])
                try:
                    observer.publish(
                        cp2, trusted_anchor=anchor1)
                except UnsafeWitness:
                    report["stale_writer_rejected"] = True
                # Replace only this disposable witness log with a valid
                # previous image and require the known-newer anchor.
                shutil.copy2(previous_image, log_path)
                try:
                    reader.inspect(trusted_anchor=anchor2)
                except UnsafeWitness:
                    report["truncated_witness_rejected_with_newer_anchor"] = True
                report["old_log_and_old_anchor_still_match"] = (
                    reader.inspect(trusted_anchor=anchor1)["latest_generation"] == 1)
                done, _ = stage("quit")
                proc.stdin.close()
                proc.wait(timeout=6)
                report["remote_fixture_exited_cleanly"] = (
                    done.get("status") == "clean_exit" and proc.returncode == 0)
            finally:
                selector.close()
    except (OSError, ValueError, RuntimeError, TimeoutError,
            subprocess.SubprocessError, json.JSONDecodeError):
        report["errors"].append("cross-node witness fixture failed closed")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()  # Only this fixture's SSH subprocess.
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    return report


def exit_status(record: dict) -> int:
    required = (
        "two_signed_checkpoints_published",
        "worker_old_state_rejected_against_newer_head",
        "truncated_witness_rejected_with_newer_anchor",
        "stale_writer_rejected",
        "old_log_and_old_anchor_still_match",
        "remote_fixture_exited_cleanly",
    )
    no_authority = (
        "signer_key_sent_to_worker", "worker_key_sent_to_observer",
        "independent_anchor_custody_verified", "source_attestation_verified",
        "agent_dispatch_authorized", "used_real_repository",
    )
    return 0 if (
        all(record.get(key) is True for key in required)
        and all(record.get(key) is False for key in no_authority)
        and record.get("provider_requests_forwarded") == 0
        and not record.get("errors")
    ) else 2


if __name__ == "__main__":
    proof = physical()
    print(json.dumps(proof, sort_keys=True, indent=2))
    raise SystemExit(exit_status(proof))
