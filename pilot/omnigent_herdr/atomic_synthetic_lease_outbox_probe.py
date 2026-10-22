#!/usr/bin/env python3
"""Fleet physical V2 atomic synthetic lease/outbox trial (no agents, no models).

Each run uses an isolated temporary 0700 directory, one fresh HMAC secret
in fixture RAM, SQLite V2 with bounded synthetic leases, and short-lived
child processes. Only this test's child PIDs may be stopped. No network
requests, deployment, real credentials, or user repositories are touched.
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
from unittest.mock import patch

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from durable_lease_store import DurableSyntheticLeaseGate, UnsafeLeaseState

WORKTREE = hashlib.sha256(b"atomic-disposable-only-worktree").hexdigest()
SCOPE = {
    "owner": "fixture-owner",
    "session": "fixture-session",
    "worktree_sha256": WORKTREE,
    "model_pin": "kilo/kilo-auto/free",
}
OP1, OP2 = "a" * 32, "b" * 32
ZERO = "0" * 64


def _child(path, secret, cmd, data, sender):
    try:
        gate = AtomicSyntheticLeaseOutbox(
            Path(path), secret, now=lambda: 1000)
        if cmd in ("fault_before", "fault_after", "debit"):
            request = data["request"]
            args = {
                "peer_uid": data["uid"],
                "expected_owner": SCOPE["owner"],
                "expected_session": SCOPE["session"],
                "expected_worktree_sha256": WORKTREE,
                "expected_model_pin": SCOPE["model_pin"],
                "operation_id": data["op"],
            }
            if cmd in ("fault_before", "fault_after"):
                original = gate._commit_outbox

                def fail_insert(db, **kw):
                    if cmd == "fault_after":
                        original(db, **kw)
                    raise UnsafeLeaseState("synthetic commit-before-return fault")

                with patch.object(gate, "_commit_outbox", side_effect=fail_insert):
                    result = gate.check(request, **args)
            else:
                result = gate.check(request, **args)
            answer = {
                "status": result["status"],
                "outbox_record": result.get("atomic_outbox_record"),
                "outbox_sha256": result.get("atomic_outbox_sha256"),
                "state_sha256": gate.state_digest(),
                "agent_dispatch_authorized": False,
            }
        elif cmd == "inspect":
            answer = {
                "status": "inspected",
                "snapshot": gate.inspect(trusted_anchor=tuple(data["anchor"])),
            }
        else:
            raise ValueError("unapproved fixture command")
        sender.send(answer)
    except Exception:
        sender.send({"status": "denied", "agent_dispatch_authorized": False})
    finally:
        sender.close()


def physical() -> dict:
    record = {
        "schema": 1,
        "observed_host": socket.gethostname(),
        "mode": "synthetic_single_sqlite_atomic_lease_outbox",
        "v2_legacy_schema_separation_verified": False,
        "pre_insert_failure_left_no_debit": False,
        "post_insert_failure_rolled_back_both": False,
        "committed_debit_and_authenticated_intent": False,
        "restart_outbox_intent_persisted": False,
        "duplicate_operation_rejected": False,
        "second_receipt_chained_in_one_db": False,
        "old_lease_image_rejected_with_newer_head": False,
        "old_image_with_old_head_still_valid": False,
        "signing_key_saved_to_disk": False,
        "independent_external_highwater_verified": False,
        "observer_publication_atomic_verified": False,
        "real_resource_authority_verified": False,
        "agent_dispatch_authorized": False,
        "model_egress_authorized": False,
        "provider_requests_forwarded": 0,
        "used_real_repository": False,
        "errors": [],
    }
    ctx = mp.get_context("fork")
    try:
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
                prefix="og-atomic-outbox-", dir=cache) as folder:
            root = Path(folder)
            os.chmod(root, 0o700)
            state_path = root / "synthetic-atomic.sqlite3"
            secret = secrets.token_bytes(32)
            gate = AtomicSyntheticLeaseOutbox(
                state_path, secret, now=lambda: 1000, create=True)
            token = gate.mint(uid=os.getuid(), **SCOPE)
            req = {
                "schema": 1, "op": "synthetic_receipt",
                "token": token, "seq": 1,
            }
            before = gate.state_digest()
            try:
                DurableSyntheticLeaseGate(state_path, secret, now=lambda: 1000)
            except UnsafeLeaseState:
                record["v2_legacy_schema_separation_verified"] = True

            def step(op: str, data: dict) -> dict:
                rx, tx = ctx.Pipe(duplex=False)
                process = ctx.Process(
                    target=_child,
                    args=(str(state_path), secret, op, data, tx),
                    name="disposable-atomic-synthetic-outbox")
                process.start()
                tx.close()
                try:
                    if not rx.poll(9):
                        raise TimeoutError("atomic outbox fixture unresponsive")
                    answer = rx.recv()
                    process.join(timeout=4)
                    if process.is_alive() or process.exitcode != 0:
                        raise RuntimeError("synthetic outbox child did not exit")
                    return answer
                finally:
                    rx.close()
                    if process.is_alive():
                        process.terminate()  # ONLY this fixture child.
                        process.join(timeout=3)

            request = {"request": req, "uid": os.getuid(), "op": OP1}
            initial = step("inspect", {"anchor": (0, ZERO)})
            fail_before = step("fault_before", request)
            after_before = step("inspect", {"anchor": (0, ZERO)})
            record["pre_insert_failure_left_no_debit"] = (
                fail_before["status"] == "denied"
                and initial["status"] == "inspected"
                and after_before["snapshot"]["event_count"] == 0
                and gate.state_digest() == before)
            fail_after = step("fault_after", request)
            after_fail = step("inspect", {"anchor": (0, ZERO)})
            record["post_insert_failure_rolled_back_both"] = (
                fail_after["status"] == "denied"
                and after_fail["snapshot"]["event_count"] == 0
                and gate.state_digest() == before)
            baseline = root / "valid-old-image"
            shutil.copy2(state_path, baseline)
            accepted = step("debit", request)
            head1 = (accepted.get("outbox_record"),
                     accepted.get("outbox_sha256"))
            current = step("inspect", {"anchor": head1})
            record["committed_debit_and_authenticated_intent"] = (
                accepted["status"] == "synthetic_receipt_only"
                and head1[0] == 1
                and current["status"] == "inspected"
                and current["snapshot"]["event_count"] == 1
                and current["snapshot"]["lease_state_sha256"]
                    == accepted["state_sha256"]
                and current["snapshot"]["agent_dispatch_authorized"] is False)
            record["restart_outbox_intent_persisted"] = (
                AtomicSyntheticLeaseOutbox(
                    state_path, secret, now=lambda: 1000).inspect(
                        trusted_anchor=head1)["event_count"] == 1)
            duplicate = step("debit", request)
            record["duplicate_operation_rejected"] = (
                duplicate["status"] == "denied"
                and step("inspect", {"anchor": head1})["status"]
                == "inspected")
            second = step("debit", {
                **request, "op": OP2, "request": {**req, "seq": 2}})
            head2 = (second.get("outbox_record"),
                     second.get("outbox_sha256"))
            record["second_receipt_chained_in_one_db"] = (
                second["status"] == "synthetic_receipt_only"
                and head2[0] == 2
                and step("inspect", {"anchor": head2})["snapshot"][
                    "event_count"] == 2)
            shutil.copy2(baseline, state_path)
            record["old_lease_image_rejected_with_newer_head"] = (
                step("inspect", {"anchor": head2})["status"] == "denied")
            reverted = step("inspect", {"anchor": (0, ZERO)})
            record["old_image_with_old_head_still_valid"] = (
                reverted["status"] == "inspected"
                and reverted["snapshot"]["event_count"] == 0
                and reverted["snapshot"]["rollback_resistance_verified"]
                    is False)
            if not all(record[key] is True for key in (
                "v2_legacy_schema_separation_verified",
                "pre_insert_failure_left_no_debit",
                "post_insert_failure_rolled_back_both",
                "committed_debit_and_authenticated_intent",
                "restart_outbox_intent_persisted",
                "duplicate_operation_rejected",
                "second_receipt_chained_in_one_db",
                "old_lease_image_rejected_with_newer_head",
                "old_image_with_old_head_still_valid",
            )):
                record["errors"].append("synthetic atomic-outbox witness incomplete")
    except (OSError, RuntimeError, ValueError, TypeError, KeyError,
            TimeoutError, EOFError):
        record["errors"].append("synthetic atomic-outbox fixture failed closed")
    return record


def exit_status(p: dict) -> int:
    expected_true = (
        "v2_legacy_schema_separation_verified",
        "pre_insert_failure_left_no_debit",
        "post_insert_failure_rolled_back_both",
        "committed_debit_and_authenticated_intent",
        "restart_outbox_intent_persisted",
        "duplicate_operation_rejected",
        "second_receipt_chained_in_one_db",
        "old_lease_image_rejected_with_newer_head",
        "old_image_with_old_head_still_valid",
    )
    expected_false = (
        "signing_key_saved_to_disk",
        "independent_external_highwater_verified",
        "observer_publication_atomic_verified",
        "real_resource_authority_verified",
        "agent_dispatch_authorized",
        "model_egress_authorized",
        "used_real_repository",
    )
    return 0 if (
        all(p.get(k) is True for k in expected_true)
        and all(p.get(k) is False for k in expected_false)
        and p.get("provider_requests_forwarded") == 0
        and not p.get("errors")
    ) else 2


if __name__ == "__main__":
    report = physical()
    print(json.dumps(report, sort_keys=True, indent=2))
    raise SystemExit(exit_status(report))
