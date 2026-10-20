#!/usr/bin/env python3
"""Fleet Commander x1↔Destroyer V2 witness-key countersignature canary.

Three ephemeral Ed25519 signing roles are separate: worker and observer
on x1-370, and countersigning witness secret ONLY on Destroyer inside
an isolated temporary 0700 cache directory (deleted after trial).
Each remote operation is a separate pinned-known-host SSH process.
No live model/provider/agent/production resource is ever contacted.
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
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from observer_checkpoint import ZERO, public_fingerprint
from v2_durable_observer_inbox import SyntheticV2DurableObserverInbox
from v2_outbox_publication import worker_publish, worker_sign_delivery
from v2_witness_countersign import verify_witness_receipt

HOST = "destroyer"
ROOT_PATTERN = re.compile(
    r"^/home/[^/]+/\.cache/og-v2-countersign-[A-Za-z0-9]{6}$")
WORKTREE = hashlib.sha256(b"v2-countersign-disposable-only").hexdigest()
WORKER = "fixture-worker"
OBSERVER = "fixture-observer"
OP = "2"*32
TRUE = (
    "distinct_three_signing_roles",
    "genesis_countersigned",
    "publication_countersigned",
    "signed_ack_fence_countersigned",
    "delivery_countersigned",
    "lost_response_readback_same_signature",
    "stale_signed_receipt_rejected_by_newer_head",
    "stale_witness_writer_denied",
    "tampered_receipt_rejected",
    "changed_witness_signing_key_rejected",
    "missing_witness_anchor_denied",
    "old_log_with_old_anchor_still_countersignable",
    "rolled_back_witness_log_denied_by_newer_anchor",
    "quarantine_countersigned_and_fenced",
    "remote_signing_key_reopened_across_processes",
)
FALSE = (
    "independent_administrative_custody_verified",
    "production_rollback_resistance_verified",
    "monotonic_external_anchor_verified",
    "real_source_transaction_attested",
    "agent_dispatch_authorized",
    "model_egress_authorized",
    "used_real_repository",
)


def physical(*, remote_root: str, remote=HOST) -> dict:
    report = {
        "schema": 1, "mode": "synthetic_v2_offhost_countersigned_receipts",
        "observer_host": socket.gethostname(), "witness_host": HOST,
        **{field: False for field in TRUE},
        **{field: False for field in FALSE},
        "provider_requests_forwarded": 0, "errors": [],
    }
    if (remote != HOST or not isinstance(remote_root, str)
            or not ROOT_PATTERN.fullmatch(remote_root)):
        report["errors"].append("unapproved fixture witness target")
        return report
    phase = "setup"
    try:
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
                prefix="og-v2-countersign-x1-", dir=cache) as tmp:
            scratch = Path(tmp)
            os.chmod(scratch, 0o700)
            worker = Ed25519PrivateKey.generate()
            observer = Ed25519PrivateKey.generate()
            worker_pin = public_fingerprint(worker.public_key())
            observer_pin = public_fingerprint(observer.public_key())
            observer_b64 = base64.b64encode(
                observer.public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw)).decode("ascii")
            gate = AtomicSyntheticLeaseOutbox(
                scratch / "worker.sqlite3",
                secrets.token_bytes(32), now=lambda: 1000, create=True)
            token = gate.mint(
                uid=os.getuid(), owner="synthetic-owner",
                session="synthetic-session",
                worktree_sha256=WORKTREE,
                model_pin="kilo/kilo-auto/free")
            before = gate.state_digest()
            request = {
                "schema": 1, "op": "synthetic_receipt", "token": token,
                "seq": 1,
            }
            accepted = gate.check(
                request, operation_id=OP, peer_uid=os.getuid(),
                expected_owner="synthetic-owner",
                expected_session="synthetic-session",
                expected_worktree_sha256=WORKTREE,
                expected_model_pin="kilo/kilo-auto/free")
            after = gate.state_digest()
            worker_outbox_head = (accepted["atomic_outbox_record"],
                                  accepted["atomic_outbox_sha256"])
            publication = worker_publish(
                signer=worker, worker_id=WORKER,
                observer_id=OBSERVER, observer_fingerprint=observer_pin,
                exported_head=gate.export_head_event(
                    trusted_anchor=worker_outbox_head))
            inbox_path = scratch / "observer.sqlite3"
            created = SyntheticV2DurableObserverInbox(
                inbox_path, observer_signer=observer,
                worker_public_key=worker.public_key(),
                observer_id=OBSERVER, worker_id=WORKER,
                initial_state=before, create=True)
            head0 = created.created_head
            def reopen():
                return SyntheticV2DurableObserverInbox(
                    inbox_path, observer_signer=observer,
                    worker_public_key=worker.public_key(),
                    observer_id=OBSERVER, worker_id=WORKER)
            def marker(head):
                return reopen().export_signed_marker(trusted_anchor=head)

            def invoke(command, *, log="primary", message=None):
                if log not in ("primary", "quarantine", "old_image"):
                    raise ValueError("unapproved witness fixture log")
                args = [
                    "ssh", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=6", remote,
                    "python3", "-B",
                    remote_root + "/v2_witness_countersign.py",
                    "--root", remote_root,
                    "--command", command,
                    "--log-name", log,
                    "--observer-public-base64", observer_b64,
                    "--observer-id", OBSERVER,
                    "--worker-id", WORKER,
                    "--worker-pin", worker_pin,
                ]
                payload = json.dumps(message, sort_keys=True) + "\n" if message is not None else ""
                p = subprocess.run(
                    args, input=payload, capture_output=True,
                    text=True, timeout=15, check=False)
                if len(p.stdout) > 10 * 1024 or len(p.stderr) > 10 * 1024:
                    raise RuntimeError("oversized remote witness reply")
                if p.returncode not in (0, 2):
                    raise RuntimeError("remote witness SSH unavailable")
                try:
                    reply = json.loads(p.stdout)
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("malformed witness JSON response") from exc
                if (not isinstance(reply, dict)
                        or reply.get("decision") != "HOLD"
                        or reply.get("agent_dispatch_authorized") is not False
                        or type(reply.get("provider_requests_forwarded")) is not int
                        or reply["provider_requests_forwarded"] != 0):
                    raise RuntimeError("unsafe witness result")
                return p.returncode, reply

            phase = "bootstrap"
            rc, boot = invoke("bootstrap")
            if rc or boot.get("schema") != 1:
                raise RuntimeError("physical witness private signer not bootstrapped")
            witness_bytes = base64.b64decode(
                boot["witness_public_base64"], validate=True)
            if len(witness_bytes) != 32:
                raise RuntimeError("invalid remote signer key size")
            witness_key = Ed25519PublicKey.from_public_bytes(witness_bytes)
            witness_pin = public_fingerprint(witness_key)
            if witness_pin != boot.get("witness_fingerprint"):
                raise RuntimeError("witness bootstrap pin mismatch")
            report["distinct_three_signing_roles"] = (
                len({worker_pin, observer_pin, witness_pin}) == 3)
            if not report["distinct_three_signing_roles"]:
                raise RuntimeError("worker/observer/witness keys overlap")
            def verify(receipt, expected_witness, expected_marker):
                return verify_witness_receipt(
                    receipt, trusted_witness_key=witness_key,
                    expected_witness_pin=witness_pin,
                    observer_public_key=observer.public_key(),
                    worker_fingerprint=worker_pin,
                    observer_id=OBSERVER, worker_id=WORKER,
                    expected_witness_head=expected_witness,
                    expected_observer_head=expected_marker)
            def append(mark, prior):
                rc, response = invoke("append", message={
                    "marker": mark, "trusted_anchor": prior})
                if rc:
                    raise RuntimeError("valid signed stage append refused")
                witness_head = tuple(response["witness_head"])
                obs_head = (mark["payload"]["revision"],
                            hashlib.sha256(_json_canonical(mark)).hexdigest())
                if (tuple(response["observer_head"]) != obs_head
                        or not verify(response["signed_receipt"], witness_head, obs_head)):
                    raise RuntimeError("witness countersignature not verified on x1")
                return witness_head, response["signed_receipt"]
            def inspect(whead, ohead, *, log="primary"):
                return invoke("inspect", log=log, message={
                    "trusted_anchor": whead, "expected_marker_head": ohead})
            def cp_remote(src, dst):
                if src not in ("primary", "old_image", "newer_image") or dst not in (
                        "primary", "old_image", "newer_image"):
                    raise ValueError("unapproved remote copy")
                p = subprocess.run([
                    "ssh", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=6", remote,
                    "cp", "-f", "--",
                    remote_root + "/" + src + ".jsonl",
                    remote_root + "/" + dst + ".jsonl",
                ], text=True, capture_output=True, timeout=12)
                if p.returncode:
                    raise RuntimeError("temporary witness image copy failure")

            def _json_canonical(value):
                return json.dumps(
                    value, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True).encode("ascii")

            phase = "genesis"
            signed_genesis_marker = marker(head0)
            w0, r0 = append(signed_genesis_marker, None)
            report["genesis_countersigned"] = verify(r0, w0, head0)
            head1 = reopen().receive(
                publication=publication,
                trusted_anchor=head0,
                expected_operation_id=OP,
                expected_outbox_head=worker_outbox_head,
                expected_after_state=after)
            signed_publication_marker = marker(head1)
            w1, r1 = append(signed_publication_marker, w0)
            report["publication_countersigned"] = verify(r1, w1, head1)
            head2, signed_ack = reopen().issue_ack(trusted_anchor=head1)
            w2, r2 = append(marker(head2), w1)
            report["signed_ack_fence_countersigned"] = (
                verify(r2, w2, head2)
                and reopen().inspect(trusted_anchor=head2)["uncertain"])
            # A fresh remote process can open the disposable witness signing
            # key and reproduce the exact same Ed25519 receipt for the exact
            # already-committed witness head after a lost response.
            rc, readback = inspect(w2, head2)
            report["lost_response_readback_same_signature"] = (
                rc == 0 and readback.get("signed_receipt") == r2
                and verify(readback["signed_receipt"], w2, head2))
            report["remote_signing_key_reopened_across_processes"] = (
                report["lost_response_readback_same_signature"]
                and verify(r0, w0, head0))
            report["stale_signed_receipt_rejected_by_newer_head"] = (
                not verify(r1, w2, head2))
            rc, _ = invoke("append", message={
                "marker": marker(head2), "trusted_anchor": w1})
            report["stale_witness_writer_denied"] = rc == 2
            rc, _ = inspect(None, head2)
            report["missing_witness_anchor_denied"] = rc == 2
            changed = json.loads(json.dumps(r2))
            changed["payload"]["observer_stage"] = "delivered"
            report["tampered_receipt_rejected"] = (
                not verify(changed, w2, head2))
            false_key = Ed25519PrivateKey.generate().public_key()
            report["changed_witness_signing_key_rejected"] = (
                not verify_witness_receipt(
                    r2, trusted_witness_key=false_key,
                    expected_witness_pin=witness_pin,
                    observer_public_key=observer.public_key(),
                    worker_fingerprint=worker_pin, observer_id=OBSERVER,
                    worker_id=WORKER,
                    expected_witness_head=w2,
                    expected_observer_head=head2))
            phase = "auxiliary_previous_stage"
            # A separate valid, earlier witness history is a disposable
            # rollback IMAGE candidate; it shares exact genesis/publication
            # bytes and the SAME ephemeral witness private key.
            rc, old0 = invoke("append", log="old_image", message={
                "marker": signed_genesis_marker, "trusted_anchor": None})
            if rc:
                raise RuntimeError("auxiliary witness genesis denied")
            rc, old1 = invoke("append", log="old_image", message={
                "marker": signed_publication_marker,
                "trusted_anchor": old0["witness_head"]})
            if rc:
                raise RuntimeError("auxiliary witness publication denied")
            old_witness_head = tuple(old1["witness_head"])
            phase = "auxiliary_actual_remote_log_restore"
            cp_remote("primary", "newer_image")
            cp_remote("old_image", "primary")
            # The physical remote log is now an actual older valid image.
            # A caller who retained the newer head MUST refuse it.
            rc_new, _ = inspect(w2, head2)
            report["rolled_back_witness_log_denied_by_newer_anchor"] = (
                rc_new == 2)
            rc_old, old_after_rewind = inspect(old_witness_head, head1)
            report["old_log_with_old_anchor_still_countersignable"] = (
                rc_old == 0 and verify(
                    old_after_rewind["signed_receipt"],
                    old_witness_head, head1))
            cp_remote("newer_image", "primary")
            # The newest retained head resumes safely, with no fallback.
            rc_newer, newer_receipt = inspect(w2, head2)
            if rc_newer != 0 or not verify(
                    newer_receipt["signed_receipt"], w2, head2):
                raise RuntimeError("newer pinned witness history not restored")
            phase = "auxiliary_quarantine"
            # Quarantine uses a new disposable observer SQLite fixture
            # and a distinct observer-witness log under the same test root.
            qfile = scratch / "quarantine.sqlite3"
            quarantine = SyntheticV2DurableObserverInbox(
                qfile, observer_signer=observer,
                worker_public_key=worker.public_key(),
                observer_id=OBSERVER, worker_id=WORKER,
                initial_state=before, create=True)
            q0 = quarantine.created_head
            q_genesis_marker = quarantine.export_signed_marker(
                trusted_anchor=q0)
            # Preserve primary completed proof; quarantine has its own
            # first-observer-genesis → terminal quarantine sequence.
            q4 = quarantine.quarantine_unverified(
                claimed_publication={"tampered": "synthetic"},
                expected_operation_id=OP, trusted_anchor=q0)
            if q0 != head0:
                raise RuntimeError("fixture genesis markers mismatched")
            rc, new0 = invoke("append", log="quarantine", message={
                "marker": q_genesis_marker, "trusted_anchor": None})
            if rc:
                raise RuntimeError("quarantine genesis not accepted")
            rc, qs = invoke("append", log="quarantine", message={
                "marker": quarantine.export_signed_marker(
                    trusted_anchor=q4),
                "trusted_anchor": new0["witness_head"]})
            report["quarantine_countersigned_and_fenced"] = (
                rc == 0 and verify(
                    qs["signed_receipt"],
                    tuple(qs["witness_head"]), q4)
                and quarantine.inspect(trusted_anchor=q4)["uncertain"])
            phase = "signed_delivery"
            delivery = worker_sign_delivery(
                signer=worker, publication=publication,
                observer_ack=signed_ack,
                observer_public_key=observer.public_key(),
                trusted_observer_fingerprint=observer_pin,
                expected_generation=2, previous_checkpoint_sha256=ZERO)
            head3 = reopen().confirm(delivery=delivery, trusted_anchor=head2)
            w3, r3 = append(marker(head3), w2)
            report["delivery_countersigned"] = (
                verify(r3, w3, head3)
                and not reopen().inspect(trusted_anchor=head3)[
                    "agent_dispatch_authorized"])
            if not all(report[k] for k in TRUE):
                report["errors"].append("physical witness countersign test incomplete")
    except (OSError, RuntimeError, ValueError, TypeError, KeyError,
            subprocess.TimeoutExpired, UnicodeError):
        report["errors"].append("offhost countersignature proof failed closed at " + phase)
    return report


def exit_status(record: dict) -> int:
    return 0 if (
        all(record.get(k) is True for k in TRUE)
        and all(record.get(k) is False for k in FALSE)
        and record.get("provider_requests_forwarded") == 0
        and not record.get("errors")
    ) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--remote", default=HOST)
    args = parser.parse_args()
    record = physical(remote_root=args.remote_root, remote=args.remote)
    print(json.dumps(record, indent=2, sort_keys=True))
    raise SystemExit(exit_status(record))
