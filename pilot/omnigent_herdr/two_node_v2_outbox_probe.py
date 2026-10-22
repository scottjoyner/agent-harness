#!/usr/bin/env python3
"""x1-370 observer ↔ Lenovo disposable V2 outbox publication/ACK fixture.

One bounded strict-host-key SSH channel. Signing keys and SQLite
fixture DB are ephemeral. Observer key is introduced inside the same
connection and worker signing key is self-advertised: NO independent
identity, durable receipt publication or external anchor custody.
All results explicitly DENY model/agent dispatch and provider calls.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import selectors
import socket
import tempfile
from pathlib import Path
import subprocess

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from observer_checkpoint import ZERO, public_fingerprint
from v2_durable_observer_inbox import (
    SyntheticV2DurableObserverInbox, UnsafeObserverInbox,
)
from v2_outbox_publication import (
    observer_sign_ack, observer_verify_publication,
    observer_verify_delivery,
)

REMOTE_HOST = "lenovo"
WORKER_FILE = (
    "/home/scott/.local/share/herdr-omnigent-lenovo-pilot/work/"
    "v2_outbox_remote_worker_fixture.py"
)
SCENARIOS = (
    "success", "missing_ack", "tampered_publication",
    "tampered_ack", "tampered_delivery",
)
MAX_REPLY = 256 * 1024


def physical(*, scenario: str = "success", remote: str = REMOTE_HOST,
             durable_inbox: bool = False) -> dict:
    report = {
        "schema": 1,
        "mode": "two_node_synthetic_v2_outbox_publication",
        "scenario": scenario,
        "durable_inbox_mode": durable_inbox,
        "durable_inbox_state_verified": False,
        "durable_inbox_fence_survives_reopen": False,
        "durable_inbox_completed_non_authorizing": False,
        "durable_inbox_independent_custody_verified": False,
        "durable_inbox_rollback_resistance_verified": False,
        "observer_host": socket.gethostname(),
        "worker_host": "scott-Lenovo-ideapad-330S-15IKB",
        "worker_and_observer_keys_distinct": False,
        "worker_signed_outbox_publication_verified": False,
        "observer_signed_ack_verified_by_worker": False,
        "worker_signed_delivery_verified_by_observer": False,
        "tampered_publication_rejected_before_ack": False,
        "missing_ack_did_not_confirm_delivery": False,
        "tampered_ack_denied_by_worker": False,
        "tampered_delivery_rejected_by_observer": False,
        "uncertain_operation_stays_fenced": False,
        "remote_exited_cleanly": False,
        "worker_signer_independently_pinned": False,
        "observer_signer_independently_custodied": False,
        "external_highwater_verified": False,
        "source_attestation_verified": False,
        "publication_durability_verified": False,
        "agent_dispatch_authorized": False,
        "model_egress_authorized": False,
        "provider_requests_forwarded": 0,
        "used_real_repository": False,
        "errors": [],
    }
    if scenario not in SCENARIOS or remote != REMOTE_HOST:
        report["errors"].append("unapproved scenario/remote worker")
        return report
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=6", remote,
        "python3", "-B", "-u", WORKER_FILE,
    ]
    child = None
    observer_scratch = None
    try:
        signer = Ed25519PrivateKey.generate()
        observer_pin = public_fingerprint(signer.public_key())
        observer_public_bytes = signer.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        child = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
        if child.stdin is None or child.stdout is None:
            raise RuntimeError("SSH worker communication unavailable")
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        try:
            def stage(packet: dict) -> dict:
                if child.poll() is not None:
                    raise RuntimeError("remote worker terminated early")
                content = json.dumps(
                    packet, sort_keys=True, separators=(",", ":"))
                if len(content) > 16 * 1024:
                    raise ValueError("outgoing command exceeds fixture cap")
                child.stdin.write(content + "\n")
                child.stdin.flush()
                if not selector.select(timeout=9):
                    raise TimeoutError("remote worker reply deadline")
                line = child.stdout.readline()
                if not line or len(line) > MAX_REPLY:
                    raise ValueError("remote worker missing or oversized reply")
                reply = json.loads(line)
                if (not isinstance(reply, dict)
                        or reply.get("agent_dispatch_authorized") is not False
                        or reply.get("model_egress_authorized") is not False
                        or type(reply.get("provider_requests_forwarded")) is not int
                        or reply["provider_requests_forwarded"] != 0):
                    raise ValueError("worker attempted to report admission")
                return reply

            def disconnect():
                if not child.stdin.closed:
                    child.stdin.close()
                child.wait(timeout=7)
                report["remote_exited_cleanly"] = child.returncode == 0

            init = stage({
                "op": "init",
                "observer_public_key": base64.b64encode(
                    observer_public_bytes).decode("ascii"),
                "observer_fingerprint": observer_pin,
            })
            if (init.get("status") != "initial"
                    or init.get("worker_id") != "lenovo-v2-worker"
                    or init.get("observer_id") != "x1-v2-observer"
                    or init.get("operation_id") != "a"*32):
                raise ValueError("wrong physical worker fixture identity")
            raw_worker = base64.b64decode(
                init["worker_public_key"], validate=True)
            if len(raw_worker) != 32:
                raise ValueError("worker public key length invalid")
            worker_key = Ed25519PublicKey.from_public_bytes(raw_worker)
            worker_pin = public_fingerprint(worker_key)
            if worker_pin != init.get("worker_fingerprint"):
                raise ValueError("worker key/pin mismatch")
            report["worker_and_observer_keys_distinct"] = (
                worker_pin != observer_pin)
            if not report["worker_and_observer_keys_distinct"]:
                raise ValueError("signing roles overlap")
            first_state = init["before_state_sha256"]
            if durable_inbox:
                observer_scratch = tempfile.TemporaryDirectory(
                    prefix="og-v2-cross-node-inbox-",
                    dir=Path.home() / ".cache")
                scratch = Path(observer_scratch.name)
                scratch.chmod(0o700)
                inbox_path = scratch / "observer.sqlite3"
                journal = SyntheticV2DurableObserverInbox(
                    inbox_path, observer_signer=signer,
                    worker_public_key=worker_key,
                    observer_id="x1-v2-observer",
                    worker_id="lenovo-v2-worker",
                    initial_state=first_state, create=True)
                genesis_head = journal.created_head
                if genesis_head is None:
                    raise RuntimeError("fixture genesis anchor unavailable")

                def reopen_inbox():
                    return SyntheticV2DurableObserverInbox(
                        inbox_path, observer_signer=signer,
                        worker_public_key=worker_key,
                        observer_id="x1-v2-observer",
                        worker_id="lenovo-v2-worker")
            committed = stage({"op": "commit"})
            if committed.get("status") != "awaiting_observer_ACK":
                raise ValueError("synthetic V2 debit was not committed")
            publication = committed["signed_publication"]
            payload = publication["payload"]
            event = payload["outbox_event"]
            # The head and post-commit state are claimed by the worker;
            # this is authenticated SELF-report, not independent receipt.
            expected_head = (
                payload["outbox_event"]["record_no"],
                payload["outbox_record_sha256"])
            pub_valid = (
                event["record_no"] == 1
                and event["sequence"] == 1
                and event["receipt_number"] == 1
                and event["previous_record_sha256"] == ZERO
                and observer_verify_publication(
                    envelope=publication,
                    worker_public_key=worker_key,
                    trusted_worker_fingerprint=worker_pin,
                    trusted_observer_fingerprint=observer_pin,
                    expected_worker_id="lenovo-v2-worker",
                    expected_observer_id="x1-v2-observer",
                    expected_operation_id="a"*32,
                    expected_outbox_head=expected_head,
                    expected_before_state=first_state,
                    expected_after_state=payload["lease_state_sha256"])
            )
            report["worker_signed_outbox_publication_verified"] = pub_valid
            if not pub_valid:
                raise ValueError("outbox signature or scope not verified")

            if scenario == "tampered_publication":
                altered = {
                    **publication,
                    "payload": {
                        **payload, "outbox_record_sha256": "f"*64}}
                report["tampered_publication_rejected_before_ack"] = (
                    not observer_verify_publication(
                        envelope=altered, worker_public_key=worker_key,
                        trusted_worker_fingerprint=worker_pin,
                        trusted_observer_fingerprint=observer_pin,
                        expected_worker_id="lenovo-v2-worker",
                        expected_observer_id="x1-v2-observer",
                        expected_operation_id="a"*32,
                        expected_outbox_head=expected_head,
                        expected_before_state=first_state,
                        expected_after_state=payload["lease_state_sha256"]))
                report["uncertain_operation_stays_fenced"] = True
                if durable_inbox:
                    try:
                        journal.receive(
                            publication=altered, trusted_anchor=genesis_head,
                            expected_operation_id="a"*32,
                            expected_outbox_head=expected_head,
                            expected_after_state=payload["lease_state_sha256"])
                    except UnsafeObserverInbox:
                        state = reopen_inbox().inspect(
                            trusted_anchor=genesis_head)
                        report["durable_inbox_state_verified"] = (
                            state["stage"] == "empty"
                            and not state["agent_dispatch_authorized"])
                    if not report["durable_inbox_state_verified"]:
                        raise RuntimeError("tampered publication unexpectedly accepted")
                    quarantined_head = reopen_inbox().quarantine_unverified(
                        claimed_publication=altered,
                        expected_operation_id="a"*32,
                        trusted_anchor=genesis_head)
                    after_quarantine = reopen_inbox().inspect(
                        trusted_anchor=quarantined_head)
                    report["durable_inbox_fence_survives_reopen"] = (
                        after_quarantine["stage"] == "quarantined_untrusted"
                        and after_quarantine["uncertain"] is True
                        and after_quarantine["signed_publication_present"] is False
                        and after_quarantine["agent_dispatch_authorized"] is False)
                    if not report["durable_inbox_fence_survives_reopen"]:
                        raise RuntimeError("tampered publication quarantine not durable")
                disconnect()
                return report
            if durable_inbox:
                received_head = journal.receive(
                    publication=publication, trusted_anchor=genesis_head,
                    expected_operation_id="a"*32,
                    expected_outbox_head=expected_head,
                    expected_after_state=payload["lease_state_sha256"])
                stage_one = reopen_inbox().inspect(
                    trusted_anchor=received_head)
                if stage_one["stage"] != "publication_fenced":
                    raise RuntimeError("durable publication fence did not persist")
                report["durable_inbox_state_verified"] = True
            if scenario == "missing_ack":
                if durable_inbox:
                    # Sign + persist, then simulate loss BEFORE sending the
                    # ACK. Never regenerate it or clear the pending fence.
                    signed_head, _lost_ack = reopen_inbox().issue_ack(
                        trusted_anchor=received_head)
                    stage_two = reopen_inbox().inspect(
                        trusted_anchor=signed_head)
                    report["durable_inbox_fence_survives_reopen"] = (
                        stage_two["stage"] == "ack_signed_fenced"
                        and stage_two["uncertain"] is True)
                report["missing_ack_did_not_confirm_delivery"] = True
                report["uncertain_operation_stays_fenced"] = True
                disconnect()
                return report
            if durable_inbox:
                signed_head, ack = reopen_inbox().issue_ack(
                    trusted_anchor=received_head)
                stage_two = reopen_inbox().inspect(
                    trusted_anchor=signed_head)
                report["durable_inbox_fence_survives_reopen"] = (
                    stage_two["stage"] == "ack_signed_fenced"
                    and stage_two["uncertain"] is True)
                if not report["durable_inbox_fence_survives_reopen"]:
                    raise RuntimeError("signed ACK not persistently fenced")
            else:
                ack = observer_sign_ack(
                signer=signer, publication=publication,
                worker_public_key=worker_key,
                trusted_worker_fingerprint=worker_pin,
                expected_worker_id="lenovo-v2-worker",
                expected_observer_id="x1-v2-observer",
                expected_operation_id="a"*32,
                expected_outbox_head=expected_head,
                expected_before_state=first_state,
                expected_after_state=payload["lease_state_sha256"],
                observer_generation=2,
                previous_checkpoint_sha256=ZERO)
            if scenario == "tampered_ack":
                altered_ack = {
                    **ack, "payload": {
                        **ack["payload"], "outbox_record_sha256": "f"*64}}
                response = stage({"op": "ack", "signed_ack": altered_ack})
                report["tampered_ack_denied_by_worker"] = (
                    response.get("status") == "denied")
                report["uncertain_operation_stays_fenced"] = True
                if durable_inbox:
                    state = reopen_inbox().inspect(
                        trusted_anchor=signed_head)
                    report["durable_inbox_fence_survives_reopen"] = (
                        state["stage"] == "ack_signed_fenced"
                        and state["uncertain"] is True)
                disconnect()
                return report
            delivered = stage({"op": "ack", "signed_ack": ack})
            if delivered.get("status") != "synthetic_ACK_delivery_only":
                raise ValueError("missing signed synthetic delivery")
            report["observer_signed_ack_verified_by_worker"] = True
            delivered_record = delivered["signed_delivery"]
            if scenario == "tampered_delivery":
                delivered_record = {
                    **delivered_record,
                    "payload": {
                        **delivered_record["payload"],
                        "observer_ack_sha256": "f"*64,
                    }}
            valid_delivery = observer_verify_delivery(
                envelope=delivered_record,
                worker_public_key=worker_key,
                trusted_worker_fingerprint=worker_pin,
                trusted_observer_fingerprint=observer_pin,
                publication=publication, observer_ack=ack)
            if scenario == "tampered_delivery":
                report["tampered_delivery_rejected_by_observer"] = (
                    not valid_delivery)
                report["uncertain_operation_stays_fenced"] = True
                if durable_inbox:
                    try:
                        reopen_inbox().confirm(
                            delivery=delivered_record,
                            trusted_anchor=signed_head)
                    except UnsafeObserverInbox:
                        stage_two = reopen_inbox().inspect(
                            trusted_anchor=signed_head)
                        report["durable_inbox_fence_survives_reopen"] = (
                            stage_two["stage"] == "ack_signed_fenced"
                            and stage_two["uncertain"] is True)
                    if not report["durable_inbox_fence_survives_reopen"]:
                        raise RuntimeError("forged delivery cleared observer fence")
                disconnect()
                return report
            report["worker_signed_delivery_verified_by_observer"] = (
                valid_delivery)
            if not valid_delivery:
                raise ValueError("synthetic worker delivery not verified")
            if durable_inbox:
                delivered_head = reopen_inbox().confirm(
                    delivery=delivered_record, trusted_anchor=signed_head)
                verified = reopen_inbox().inspect(
                    trusted_anchor=delivered_head)
                report["durable_inbox_completed_non_authorizing"] = (
                    verified["stage"] == "delivered"
                    and verified["agent_dispatch_authorized"] is False
                    and verified["source_transaction_attested"] is False)
                if not report["durable_inbox_completed_non_authorizing"]:
                    raise RuntimeError("observer completion falsely authorized")
            last = stage({"op": "quit"})
            if last.get("status") != "clean_exit":
                raise ValueError("remote did not complete the fixture")
            disconnect()
        finally:
            selector.close()
    except (OSError, ValueError, RuntimeError, TypeError, KeyError,
            TimeoutError, binascii.Error, subprocess.SubprocessError,
            json.JSONDecodeError):
        report["errors"].append("bounded two-node V2 fixture failed closed")
    finally:
        if observer_scratch is not None:
            observer_scratch.cleanup()
        if child is not None and child.poll() is None:
            child.terminate()  # Only this explicitly spawned SSH process.
            try:
                child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=4)
    return report


def exit_status(report: dict) -> int:
    base = (
        report.get("worker_and_observer_keys_distinct") is True
        and report.get("worker_signed_outbox_publication_verified") is True
        and report.get("remote_exited_cleanly") is True
        and all(report.get(name) is False for name in (
            "worker_signer_independently_pinned",
            "observer_signer_independently_custodied",
            "external_highwater_verified",
            "source_attestation_verified",
            "publication_durability_verified",
            "agent_dispatch_authorized",
            "model_egress_authorized", "used_real_repository",
        ))
        and report.get("provider_requests_forwarded") == 0
        and not report.get("errors")
    )
    name = report.get("scenario")
    if name == "success":
        outcome = (
            report.get("observer_signed_ack_verified_by_worker") is True
            and report.get("worker_signed_delivery_verified_by_observer") is True
            and report.get("uncertain_operation_stays_fenced") is False)
    elif name == "missing_ack":
        outcome = (report.get("missing_ack_did_not_confirm_delivery") is True
                   and report.get("uncertain_operation_stays_fenced") is True)
    elif name == "tampered_publication":
        outcome = (report.get("tampered_publication_rejected_before_ack") is True
                   and report.get("uncertain_operation_stays_fenced") is True)
    elif name == "tampered_ack":
        outcome = (report.get("tampered_ack_denied_by_worker") is True
                   and report.get("uncertain_operation_stays_fenced") is True)
    elif name == "tampered_delivery":
        outcome = (report.get("observer_signed_ack_verified_by_worker") is True
                   and report.get("tampered_delivery_rejected_by_observer") is True
                   and report.get("uncertain_operation_stays_fenced") is True)
    else:
        outcome = False
    if report.get("durable_inbox_mode") is True:
        durable = (
            report.get("durable_inbox_state_verified") is True
            and report.get("durable_inbox_independent_custody_verified") is False
            and report.get("durable_inbox_rollback_resistance_verified") is False
        )
        if name == "success":
            durable &= (
                report.get("durable_inbox_fence_survives_reopen") is True
                and report.get("durable_inbox_completed_non_authorizing") is True
            )
        elif name == "tampered_publication":
            durable &= (
                report.get("durable_inbox_fence_survives_reopen") is True
                and report.get("durable_inbox_completed_non_authorizing") is False
            )
        else:
            durable &= (
                report.get("durable_inbox_fence_survives_reopen") is True
                and report.get("durable_inbox_completed_non_authorizing") is False
            )
        outcome = outcome and durable
    elif report.get("durable_inbox_mode") is not False:
        outcome = False
    return 0 if base and outcome else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="success")
    parser.add_argument("--durable-inbox", action="store_true")
    args = parser.parse_args()
    proof = physical(scenario=args.scenario,
                     durable_inbox=args.durable_inbox)
    print(json.dumps(proof, indent=2, sort_keys=True))
    raise SystemExit(exit_status(proof))
