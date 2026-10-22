#!/usr/bin/env python3
"""Physical x1 observer ↔ Destroyer cooperative stage-witness validation.

Requires an operator-created temporary 0700 remote directory that
contains the research-only verifier and its Python dependencies.
Every remote operation launches a new strict-host-key SSH process.
Local observer and worker SQLite files, credentials and signing keys
are disposable; no model/agent/provider calls. Never performs host
service changes, automatic refunds, retries or witness promotion.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from observer_checkpoint import ZERO, public_fingerprint
from v2_durable_observer_inbox import (
    SyntheticV2DurableObserverInbox, UnsafeObserverInbox,
)
from v2_outbox_publication import worker_publish, worker_sign_delivery

HOST = "destroyer"
ROOT_PATTERN = re.compile(
    r"^/home/[^/]+/\.cache/og-v2-offhost-[A-Za-z0-9]{6}$")
WORKTREE = hashlib.sha256(b"offhost-disposable-research-fixture").hexdigest()
OP = "a" * 32
WORKER = "lenovo-v2-synthetic"
OBSERVER = "x1-v2-synthetic"


def physical(*, remote_root: str, remote=HOST) -> dict:
    result = {
        "schema": 1, "mode": "synthetic_v2_physical_offhost_marker_witness",
        "observer_host": socket.gethostname(), "witness_host": HOST,
        "signed_genesis_witnessed": False,
        "publication_stage_witnessed": False,
        "ack_fence_witnessed": False,
        "delivery_stage_witnessed": False,
        "rolled_back_observer_rejected_by_newer_witness": False,
        "stale_witness_writer_denied": False,
        "offhost_log_rollback_denied_with_newer_anchor": False,
        "old_witness_log_and_old_anchor_still_match": False,
        "quarantine_marker_witnessed": False,
        "remote_process_exits_cleanly": False,
        "witness_administratively_independent": False,
        "worker_source_attested": False,
        "external_highwater_immutability_verified": False,
        "persistent_trust_root_verified": False,
        "production_rollback_resistance_verified": False,
        "agent_dispatch_authorized": False,
        "model_egress_authorized": False,
        "provider_requests_forwarded": 0, "used_real_repository": False,
        "errors": [],
    }
    if remote != HOST or not isinstance(remote_root, str) or not ROOT_PATTERN.fullmatch(remote_root):
        result["errors"].append("unapproved offhost fixture target")
        return result
    try:
        with tempfile.TemporaryDirectory(
                prefix="og-v2-offhost-x1-", dir=Path.home() / ".cache") as scratch:
            local = Path(scratch)
            os.chmod(local, 0o700)
            worker_db = local / "worker.sqlite3"
            observer_db = local / "observer.sqlite3"
            hmac_key = secrets.token_bytes(32)
            worker_signer = Ed25519PrivateKey.generate()
            observer_signer = Ed25519PrivateKey.generate()
            observer_pin = public_fingerprint(observer_signer.public_key())
            worker_pin = public_fingerprint(worker_signer.public_key())
            key_b64 = base64.b64encode(
                observer_signer.public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw)).decode("ascii")

            def witness(command, log_name, payload):
                if not re.fullmatch(r"[a-z][a-z0-9_-]{0,28}", log_name):
                    raise ValueError("untrusted witness log identity")
                args = [
                    "ssh", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=6", remote,
                    "python3", "-B",
                    remote_root + "/v2_offhost_marker_witness.py",
                    "--path", remote_root + "/" + log_name + ".jsonl",
                    "--operation", command,
                    "--observer-public-base64", key_b64,
                    "--observer-id", OBSERVER,
                    "--worker-id", WORKER,
                    "--worker-pin", worker_pin,
                ]
                process = subprocess.run(
                    args, input=json.dumps(payload, sort_keys=True) + "\n",
                    text=True, capture_output=True, timeout=15,
                    check=False)
                if len(process.stdout) > 8192 or len(process.stderr) > 8192:
                    raise RuntimeError("offhost fixture response too large")
                if process.returncode not in (0, 2):
                    raise RuntimeError("SSH or offhost witness unavailable")
                try:
                    data = json.loads(process.stdout)
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("invalid offhost witness response") from exc
                if (data.get("decision") != "HOLD"
                        or data.get("agent_dispatch_authorized") is not False
                        or type(data.get("provider_requests_forwarded")) is not int
                        or data["provider_requests_forwarded"] != 0):
                    raise RuntimeError("offhost witness output not deny-only")
                return process.returncode, data

            def observe_inbox(head):
                return SyntheticV2DurableObserverInbox(
                    observer_db, observer_signer=observer_signer,
                    worker_public_key=worker_signer.public_key(),
                    observer_id=OBSERVER, worker_id=WORKER).inspect(
                        trusted_anchor=head)

            def export(head):
                return SyntheticV2DurableObserverInbox(
                    observer_db, observer_signer=observer_signer,
                    worker_public_key=worker_signer.public_key(),
                    observer_id=OBSERVER, worker_id=WORKER).export_signed_marker(
                        trusted_anchor=head)

            def append(log_name, marker, prior=None):
                rc, data = witness(
                    "genesis" if prior is None else "append",
                    log_name, {"marker": marker, "trusted_anchor": prior})
                if rc != 0:
                    raise RuntimeError("valid offhost stage rejected")
                return tuple(data["anchor"])

            def inspect(log_name, witness_head, observer_head):
                return witness("inspect", log_name, {
                    "trusted_anchor": witness_head,
                    "expected_marker_head": observer_head})

            def cp_remote(src_name, dest_name):
                # All filenames restricted to this ephemeral witness root.
                for value in (src_name, dest_name):
                    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,28}", value):
                        raise ValueError("unapproved backup file name")
                src = remote_root + "/" + src_name + ".jsonl"
                dst = remote_root + "/" + dest_name + ".jsonl"
                p = subprocess.run([
                    "ssh", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=6", remote,
                    "cp", "-f", "--", src, dst,
                ], text=True, capture_output=True, timeout=10)
                if p.returncode != 0:
                    raise RuntimeError("fixture-only remote file copy failed")

            gate = AtomicSyntheticLeaseOutbox(
                worker_db, hmac_key, create=True, now=lambda: 1000)
            token = gate.mint(
                uid=os.getuid(), owner="fixture-owner",
                session="fixture-session",
                worktree_sha256=WORKTREE, model_pin="kilo/kilo-auto/free")
            before = gate.state_digest()
            accepted = gate.check(
                {"schema": 1, "op": "synthetic_receipt",
                 "token": token, "seq": 1},
                operation_id=OP,
                peer_uid=os.getuid(),
                expected_owner="fixture-owner",
                expected_session="fixture-session",
                expected_worktree_sha256=WORKTREE,
                expected_model_pin="kilo/kilo-auto/free")
            after = gate.state_digest()
            outbox_head = (1, accepted["atomic_outbox_sha256"])
            pub = worker_publish(
                signer=worker_signer, worker_id=WORKER,
                observer_id=OBSERVER, observer_fingerprint=observer_pin,
                exported_head=gate.export_head_event(
                    trusted_anchor=outbox_head))
            observer = SyntheticV2DurableObserverInbox(
                observer_db, observer_signer=observer_signer,
                worker_public_key=worker_signer.public_key(),
                observer_id=OBSERVER, worker_id=WORKER,
                initial_state=before, create=True)
            g = observer.created_head
            m0 = export(g)
            w0 = append("primary", m0)
            rc, check = inspect("primary", w0, g)
            result["signed_genesis_witnessed"] = (
                rc == 0 and check["marker_stage"] == "empty")
            i1 = observer.receive(
                publication=pub, trusted_anchor=g,
                expected_operation_id=OP,
                expected_outbox_head=outbox_head,
                expected_after_state=after)
            m1 = export(i1)
            w1 = append("primary", m1, prior=w0)
            rc, seen = inspect("primary", w1, i1)
            result["publication_stage_witnessed"] = (
                rc == 0 and seen["marker_stage"] == "publication_fenced")
            old_inbox = local / "valid-old-inbox.sqlite3"
            shutil.copy2(observer_db, old_inbox)
            i2, ack = observer.issue_ack(trusted_anchor=i1)
            m2 = export(i2)
            w2 = append("primary", m2, prior=w1)
            rc, check = inspect("primary", w2, i2)
            result["ack_fence_witnessed"] = (
                rc == 0 and check["marker_stage"] == "ack_signed_fenced"
                and observe_inbox(i2)["uncertain"] is True)
            # Replace the local observer SQLite file with its previous,
            # individually valid signed stage-1 image. Retain the newer
            # witness log/anchor on Destroyer. The witness must disagree
            # with the ACTUAL reopened older observer DB, not just with
            # a manually supplied stale hash.
            new_inbox = local / "valid-new-inbox.sqlite3"
            shutil.copy2(observer_db, new_inbox)
            shutil.copy2(old_inbox, observer_db)
            reopened_old = observe_inbox(i1)
            rolled_back_head = (
                reopened_old["revision"], reopened_old["head_sha256"])
            rc_old, _ = inspect("primary", w2, rolled_back_head)
            result["rolled_back_observer_rejected_by_newer_witness"] = (
                reopened_old["stage"] == "publication_fenced"
                and rc_old == 2)
            shutil.copy2(new_inbox, observer_db)
            if observe_inbox(i2)["stage"] != "ack_signed_fenced":
                raise RuntimeError("fixture newer observer DB recovery failed")
            rc_stale, _ = witness(
                "append", "primary",
                {"marker": m2, "trusted_anchor": w1})
            result["stale_witness_writer_denied"] = rc_stale == 2
            cp_remote("primary", "newer_backup")
            cp_remote("primary", "rollback_snapshot")
            # Simulate a witness rollback using an older valid fixture image.
            # The earlier record must still exist: preserve first two stages.
            # Build an earlier valid log as separate append-only file.
            wold0 = append("old_image", m0)
            wold1 = append("old_image", m1, prior=wold0)
            cp_remote("old_image", "primary")
            rc_new, _ = inspect("primary", w2, i2)
            result["offhost_log_rollback_denied_with_newer_anchor"] = rc_new == 2
            rc_old_head, old_view = inspect("primary", wold1, i1)
            result["old_witness_log_and_old_anchor_still_match"] = (
                rc_old_head == 0 and old_view["marker_stage"] == "publication_fenced")
            cp_remote("newer_backup", "primary")
            delivery = worker_sign_delivery(
                signer=worker_signer, publication=pub, observer_ack=ack,
                observer_public_key=observer_signer.public_key(),
                trusted_observer_fingerprint=observer_pin,
                expected_generation=2, previous_checkpoint_sha256=ZERO)
            i3 = observer.confirm(delivery=delivery, trusted_anchor=i2)
            m3 = export(i3)
            w3 = append("primary", m3, prior=w2)
            rc, checked = inspect("primary", w3, i3)
            result["delivery_stage_witnessed"] = (
                rc == 0 and checked["marker_stage"] == "delivered"
                and observe_inbox(i3)["agent_dispatch_authorized"] is False)

            # Independent quarantine demonstration: same physical witness,
            # fresh separate log & inbox; no false signed ACK is allowed.
            isolated_observer = local / "quarantine.sqlite3"
            q_inbox = SyntheticV2DurableObserverInbox(
                isolated_observer, observer_signer=observer_signer,
                worker_public_key=worker_signer.public_key(),
                observer_id=OBSERVER, worker_id=WORKER,
                initial_state=before, create=True)
            q0 = q_inbox.created_head
            qlog0 = append("quarantine", q_inbox.export_signed_marker(
                trusted_anchor=q0))
            q4 = q_inbox.quarantine_unverified(
                claimed_publication={"suspect": "bad signature"},
                expected_operation_id=OP, trusted_anchor=q0)
            qlog4 = append("quarantine", q_inbox.export_signed_marker(
                trusted_anchor=q4), prior=qlog0)
            rc, observed = inspect("quarantine", qlog4, q4)
            result["quarantine_marker_witnessed"] = (
                rc == 0 and observed["marker_stage"] == "quarantined_untrusted")
            result["remote_process_exits_cleanly"] = True

            required = (
                "signed_genesis_witnessed", "publication_stage_witnessed",
                "ack_fence_witnessed", "delivery_stage_witnessed",
                "rolled_back_observer_rejected_by_newer_witness",
                "stale_witness_writer_denied",
                "offhost_log_rollback_denied_with_newer_anchor",
                "old_witness_log_and_old_anchor_still_match",
                "quarantine_marker_witnessed",
                "remote_process_exits_cleanly",
            )
            if not all(result[k] for k in required):
                result["errors"].append("physical offhost witness incomplete")
    except (OSError, RuntimeError, KeyError, ValueError, TypeError,
            subprocess.TimeoutExpired, UnsafeObserverInbox):
        result["errors"].append("physical offhost experiment failed closed")
    return result


def exit_status(result: dict) -> int:
    true_fields = (
        "signed_genesis_witnessed", "publication_stage_witnessed",
        "ack_fence_witnessed", "delivery_stage_witnessed",
        "rolled_back_observer_rejected_by_newer_witness",
        "stale_witness_writer_denied",
        "offhost_log_rollback_denied_with_newer_anchor",
        "old_witness_log_and_old_anchor_still_match",
        "quarantine_marker_witnessed", "remote_process_exits_cleanly",
    )
    false_fields = (
        "witness_administratively_independent",
        "worker_source_attested",
        "external_highwater_immutability_verified",
        "persistent_trust_root_verified",
        "production_rollback_resistance_verified",
        "agent_dispatch_authorized", "model_egress_authorized",
        "used_real_repository",
    )
    return 0 if (
        all(result.get(k) is True for k in true_fields)
        and all(result.get(k) is False for k in false_fields)
        and result.get("provider_requests_forwarded") == 0
        and not result.get("errors")
    ) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--remote", default=HOST)
    args = parser.parse_args()
    data = physical(remote_root=args.remote_root, remote=args.remote)
    print(json.dumps(data, sort_keys=True, indent=2))
    raise SystemExit(exit_status(data))
