#!/usr/bin/env python3
"""Physical x1 signed worker intent ↔ Destroyer LOCAL synthetic source debit.

Destroyer actually commits a synthetic SQLite balance decrement AND source
Ed25519-signed receipt in one local transaction, then a new SSH process
opens the local resource state to verify it. The two nodes remain under
shared operator custody, and worker/source transactions are not atomic
together. No user files, agents, provider calls, services or keys persist.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from atomic_synthetic_lease_outbox import AtomicSyntheticLeaseOutbox
from observer_checkpoint import ZERO, public_fingerprint
from v2_outbox_publication import worker_publish
from v2_source_transaction_fixture import (
    _state_digest, verify_source_receipt,
    verify_stale_source_fence,
    verify_source_corresponds_to_signed_worker_intent,
)
from v2_cross_host_uncertainty import classify_source_recovery
from v2_uncertainty_journal import (
    SyntheticWorkerUncertaintyJournal, context_digest,
)

SOURCE_HOST = "destroyer"
ROOT_RE = re.compile(
    r"^/home/[^/]+/\.cache/og-v2-source-[A-Za-z0-9]{6}$")
WORKTREE = hashlib.sha256(b"source-physical-disposable-synthetic").hexdigest()
OP1 = "1"*32
OP2 = "2"*32
STALE_OP = "e"*32
TRUE = (
    "source_private_key_on_remote_only",
    "source_sqlite_debit_committed",
    "source_receipt_signed_and_pinned",
    "worker_intent_and_source_receipt_match",
    "source_readback_after_process_restart",
    "duplicate_operation_denied",
    "duplicate_intent_denied_by_stale_source_head",
    "stale_source_attempt_signed_and_persisted",
    "stale_attempt_rebase_refused",
    "worker_publication_forgery_denied",
    "wrong_source_key_denied",
    "source_receipt_mutation_denied",
    "second_synthetic_debit_committed",
    "old_source_sqlite_rejected_by_newer_head",
    "old_source_sqlite_with_old_head_still_valid",
    "restored_new_source_head_verified",
    "source_budget_exhaustion_denied",
    "crash_before_source_commit_left_genesis",
    "crash_before_commit_did_not_authorize_retry",
    "crash_after_commit_returned_no_reply",
    "read_only_recovery_proves_committed_source",
    "recovery_did_not_redebit",
    "recovery_holds_dispatch",
    "partition_without_readback_uncertain",
    "malformed_recovery_quarantined",
    "wrong_pinned_recovery_quarantined",
    "repeat_commit_after_recovery_denied",
    "durable_worker_uncertainty_before_source_recovery",
    "durable_worker_signed_evidence_after_source_recovery",
    "worker_journal_fresh_process_readback",
    "worker_journal_denies_unpinned_old_head",
    "journal_crash_before_append_left_existing_head",
    "journal_crash_after_append_returned_no_reply",
    "journal_lost_ack_read_only_reconciled",
    "journal_duplicate_append_after_lost_ack_denied",
    "journal_reconcile_did_not_redebit_source",
)
FALSE = (
    "independently_custodied_source_verified",
    "production_resource_transaction_attested",
    "distributed_transaction_atomicity_verified",
    "production_rollback_resistance_verified",
    "agent_dispatch_authorized", "model_egress_authorized",
    "used_real_repository",
)


def physical(*, remote_root: str, remote: str = SOURCE_HOST) -> dict:
    record = {
        "schema": 1, "mode": "synthetic_v2_resource_side_commit_probe",
        "worker_host": socket.gethostname(), "resource_host": SOURCE_HOST,
        **{name: False for name in TRUE},
        **{name: False for name in FALSE},
        "provider_requests_forwarded": 0, "errors": [],
    }
    if (remote != SOURCE_HOST or not isinstance(remote_root, str)
            or ROOT_RE.fullmatch(remote_root) is None):
        record["errors"].append("unapproved physical resource fixture")
        return record
    stage = "start"
    try:
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        with (tempfile.TemporaryDirectory(
                prefix="og-v2-source-x1-", dir=cache) as dirname,
                tempfile.TemporaryDirectory(
                    prefix="og-v2-uncertain-", dir=cache) as journal_dir):
            here = Path(dirname)
            here.chmod(0o700)
            worker = Ed25519PrivateKey.generate()
            observer = Ed25519PrivateKey.generate()
            worker_pin = public_fingerprint(worker.public_key())
            observer_pin = public_fingerprint(observer.public_key())

            def remote_source(command: str, message: dict, *, fault="none"):
                if command not in ("bootstrap", "commit", "inspect", "recover", "fence"):
                    raise RuntimeError("forbidden source operation")
                if (fault not in ("none", "crash-before-source-commit",
                                  "crash-after-source-commit")
                        or (fault != "none" and command != "commit")):
                    raise RuntimeError("invalid disposable fault injection")
                args = [
                    "ssh", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=6", remote,
                    "python3", "-B",
                    remote_root + "/v2_source_transaction_fixture.py",
                    "--root", remote_root, "--command", command,
                    "--research-fault", fault]
                p = subprocess.run(
                    args, input=json.dumps(message, sort_keys=True) + "\n",
                    text=True, capture_output=True, timeout=16,
                    check=False)
                if p.returncode in (96, 97) and fault != "none":
                    # Explicitly *no* answer: the same disposable CLI
                    # process exited at a fixed fault point. The caller
                    # cannot infer whether its request committed solely
                    # from a missing reply.
                    if p.stdout or p.stderr:
                        raise RuntimeError("unexpected fault output")
                    return p.returncode, None
                if p.returncode not in (0, 2) or len(p.stdout) > 8192:
                    raise RuntimeError("remote synthetic source unavailable")
                try:
                    reply = json.loads(p.stdout)
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("untrusted source response") from exc
                if (not isinstance(reply, dict)
                        or reply.get("decision") != "HOLD"
                        or reply.get("agent_dispatch_authorized") is not False
                        or type(reply.get("provider_requests_forwarded")) is not int
                        or reply["provider_requests_forwarded"] != 0):
                    raise RuntimeError("source response granted unauthorized action")
                return p.returncode, reply

            def copy_image(source_name: str, target_name: str):
                allowed = {
                    "source-debits.sqlite3", "old-source.sqlite3",
                    "new-source.sqlite3",
                }
                if source_name not in allowed or target_name not in allowed:
                    raise RuntimeError("forbidden resource snapshot path")
                args = [
                    "ssh", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=6", remote,
                    "cp", "-f", "--",
                    remote_root + "/" + source_name,
                    remote_root + "/" + target_name,
                ]
                p = subprocess.run(
                    args, text=True, capture_output=True, timeout=12)
                if p.returncode:
                    raise RuntimeError("source fixture snapshot copy failed")

            stage = "bootstrap"
            rc, genesis = remote_source("bootstrap", {})
            if rc or genesis.get("schema") != 1:
                raise RuntimeError("physical source bootstrap denied")
            source_public_raw = base64.b64decode(
                genesis["source_public_base64"], validate=True)
            if len(source_public_raw) != 32:
                raise RuntimeError("source role key not Ed25519")
            source_pub = Ed25519PublicKey.from_public_bytes(source_public_raw)
            source_pin = public_fingerprint(source_pub)
            record["source_private_key_on_remote_only"] = (
                genesis["source_fingerprint"] == source_pin
                and source_pin not in (worker_pin, observer_pin)
                and genesis["balance"] == 2
                and genesis["head"] == [0, ZERO])

            gate = AtomicSyntheticLeaseOutbox(
                here / "worker.sqlite3", os.urandom(32),
                now=lambda: 1000, create=True)
            token = gate.mint(
                uid=os.getuid(), owner="test-owner",
                session="test-session", worktree_sha256=WORKTREE,
                model_pin="kilo/kilo-auto/free")
            before = gate.state_digest()
            accepted = gate.check(
                {"schema": 1, "op": "synthetic_receipt", "token": token,
                 "seq": 1},
                peer_uid=os.getuid(), operation_id=OP1,
                expected_owner="test-owner", expected_session="test-session",
                expected_worktree_sha256=WORKTREE,
                expected_model_pin="kilo/kilo-auto/free")
            if accepted.get("status") != "synthetic_receipt_only":
                raise RuntimeError("worker source intent not synthetically accepted")
            after = gate.state_digest()
            worker_head = (accepted["atomic_outbox_record"],
                           accepted["atomic_outbox_sha256"])
            publication = worker_publish(
                signer=worker, worker_id="worker",
                observer_id="observer",
                observer_fingerprint=observer_pin,
                exported_head=gate.export_head_event(
                    trusted_anchor=worker_head))
            source_intent = publication["payload"]["outbox_event"]["request_sha256"]
            stage = "worker_uncertainty_journal_setup"
            jctx = {
                "schema": 1,
                "kind": "synthetic_v2_uncertain_worker_intent",
                "operation_id": OP1,
                "worker_public_base64": base64.b64encode(
                    worker.public_key().public_bytes(
                        encoding=serialization.Encoding.Raw,
                        format=serialization.PublicFormat.Raw,
                    )).decode("ascii"),
                "worker_pin": worker_pin,
                "observer_pin": observer_pin,
                "source_public_base64": genesis["source_public_base64"],
                "source_pin": source_pin,
                "worker_id": "worker", "observer_id": "observer",
                "worker_outbox_head": list(worker_head),
                "worker_before_state": before,
                "worker_after_state": after,
                "prior_source_head": [0, ZERO],
            }
            jctx_hash = context_digest(jctx)
            journal = SyntheticWorkerUncertaintyJournal(
                Path(journal_dir), create=True, context=jctx,
                worker_publication=publication)
            journal_head = (0, jctx_hash)

            def worker_journal_fresh_readback(head):
                command = [
                    "python3", "-B",
                    str(Path(__file__).with_name("v2_uncertainty_journal.py")),
                    "--root", journal_dir, "--command", "inspect",
                    "--context-sha256", jctx_hash,
                    "--head", str(head[0]) + ":" + head[1]]
                r = subprocess.run(
                    command, capture_output=True, text=True,
                    check=False, timeout=12)
                if (r.returncode not in (0, 2) or len(r.stdout) > 8192):
                    raise RuntimeError("worker recovery journal subprocess failed")
                return r.returncode, json.loads(r.stdout)

            def journal_one_shot(command, head, *, reply=None, fault="none"):
                if command not in ("observe", "reconcile", "inspect"):
                    raise RuntimeError("unexpected journal fixture action")
                if (fault not in ("none", "crash-before-journal-commit",
                                  "crash-after-journal-commit")
                        or (fault != "none" and command != "observe")):
                    raise RuntimeError("bad disposable journal fault")
                argv = [
                    "python3", "-B",
                    str(Path(__file__).with_name("v2_uncertainty_journal.py")),
                    "--root", journal_dir, "--command", command,
                    "--context-sha256", jctx_hash,
                    "--head", str(head[0]) + ":" + head[1],
                    "--research-fault", fault]
                r = subprocess.run(
                    argv,
                    input=json.dumps({"source_reply": reply}, sort_keys=True)+"\n",
                    capture_output=True, text=True, timeout=12,
                    check=False)
                if r.returncode in (96, 97) and fault != "none":
                    if r.stdout or r.stderr:
                        raise RuntimeError("unexpected interrupted worker ACK")
                    return r.returncode, None
                if r.returncode not in (0, 2) or len(r.stdout) > 8192:
                    raise RuntimeError("untrusted worker journal subprocess")
                data = json.loads(r.stdout)
                if (not isinstance(data, dict) or data.get("decision") != "HOLD"
                        or data.get("agent_dispatch_authorized") is not False
                        or type(data.get("provider_requests_forwarded")) is not int
                        or data["provider_requests_forwarded"] != 0):
                    raise RuntimeError("journal fixture returned active grant")
                return r.returncode, data

            stage = "source_fault_injection_preparation"
            recovery_scope = {
                "worker_publication": publication,
                "worker_public_key": worker.public_key(),
                "trusted_worker_pin": worker_pin,
                "trusted_observer_pin": observer_pin,
                "expected_worker_id": "worker",
                "expected_observer_id": "observer",
                "expected_operation_id": OP1,
                "expected_worker_outbox_head": worker_head,
                "expected_worker_before_state": before,
                "expected_worker_after_state": after,
                "source_public_key": source_pub,
                "trusted_source_pin": source_pin,
                "trusted_prior_source_head": (0, ZERO),
            }
            recover_req = {
                "operation_id": OP1,
                "intent_sha256": source_intent,
                "trusted_prior_head": [0, ZERO],
            }
            first_req = {
                "operation_id": OP1,
                "intent_sha256": source_intent,
                "trusted_head": [0, ZERO],
            }
            stage = "crash_before_local_commit"
            crash_rc, crash_reply = remote_source(
                "commit", first_req, fault="crash-before-source-commit")
            missing_rc, missing = remote_source("recover", recover_req)
            genesis_rc, genesis_after = remote_source(
                "inspect", {"trusted_head": [0, ZERO]})
            record["crash_before_source_commit_left_genesis"] = (
                crash_rc == 96 and crash_reply is None
                and missing_rc == 2 and genesis_rc == 0
                and genesis_after.get("balance") == 2)
            record["crash_before_commit_did_not_authorize_retry"] = (
                classify_source_recovery(
                    source_reply=missing, **recovery_scope)["status"]
                == "UNCERTAIN_HOLD"
                and classify_source_recovery(
                    source_reply=None, **recovery_scope)[
                        "automatic_retry_authorized"] is False)
            journal_uncertain = journal.observe(
                missing, expected_context_sha256=jctx_hash,
                trusted_prior_journal_head=journal_head)
            journal_head = tuple(journal_uncertain["journal_head"])
            record["durable_worker_uncertainty_before_source_recovery"] = (
                journal_uncertain["status"] == "UNCERTAIN_HOLD"
                and journal_uncertain["automatic_retry_authorized"] is False)

            stage = "crash_after_local_commit_before_reply"
            lost_rc, lost_reply = remote_source(
                "commit", first_req, fault="crash-after-source-commit")
            record["crash_after_commit_returned_no_reply"] = (
                lost_rc == 97 and lost_reply is None)
            record["partition_without_readback_uncertain"] = (
                classify_source_recovery(
                    source_reply=None, **recovery_scope)["status"]
                    == "UNCERTAIN_HOLD")
            journal_no_reply = journal.observe(
                None, expected_context_sha256=jctx_hash,
                trusted_prior_journal_head=journal_head)
            journal_head = tuple(journal_no_reply["journal_head"])
            stage = "read_only_post_crash_recovery"
            rc, one = remote_source("recover", recover_req)
            if rc or one.get("balance") != 1:
                raise RuntimeError("signed source readback failed after lost reply")
            verdict = classify_source_recovery(
                source_reply=one, **recovery_scope)
            record["read_only_recovery_proves_committed_source"] = (
                verdict["status"] == "SOURCE_LOCAL_COMMITTED_HOLD"
                and verdict["source_local_commit_verified"] is True)
            stage = "crash_before_worker_journal_commit"
            prior_jhead = journal_head
            before_jrc, before_jreply = journal_one_shot(
                "observe", prior_jhead, reply=one,
                fault="crash-before-journal-commit")
            check_before_rc, check_before_reply = journal_one_shot(
                "inspect", prior_jhead)
            record["journal_crash_before_append_left_existing_head"] = (
                before_jrc == 96 and before_jreply is None
                and check_before_rc == 0
                and check_before_reply["journal_head"] == list(prior_jhead)
                and check_before_reply["status"] == "UNCERTAIN_HOLD")

            stage = "crash_after_worker_journal_commit_before_ack"
            after_jrc, after_jreply = journal_one_shot(
                "observe", prior_jhead, reply=one,
                fault="crash-after-journal-commit")
            record["journal_crash_after_append_returned_no_reply"] = (
                after_jrc == 97 and after_jreply is None)
            stage = "read_only_worker_journal_lost_ack_reconcile"
            reconciled_rc, journal_verified = journal_one_shot(
                "reconcile", prior_jhead, reply=one)
            if reconciled_rc or journal_verified is None:
                raise RuntimeError(
                    "signed journal append not recovered after lost ACK")
            journal_head = tuple(journal_verified["journal_head"])
            record["journal_lost_ack_read_only_reconciled"] = (
                journal_verified["status"] == "SOURCE_LOCAL_COMMITTED_HOLD"
                and journal_verified["journal_head"][0] == prior_jhead[0]+1
                and journal_verified["source_local_commit_verified"] is True
                and journal_verified["automatic_retry_authorized"] is False)

            duplicate_jrc, duplicate_jreply = journal_one_shot(
                "observe", prior_jhead, reply=one)
            record["journal_duplicate_append_after_lost_ack_denied"] = (
                duplicate_jrc == 2 and duplicate_jreply is not None
                and duplicate_jreply["status"] == "QUARANTINED_HOLD")
            source_after_jrc, source_after_jreply = remote_source(
                "inspect", {"trusted_head": list(one["head"])})
            record["journal_reconcile_did_not_redebit_source"] = (
                source_after_jrc == 0
                and source_after_jreply.get("balance") == 1
                and source_after_jreply.get("receipt") == one["receipt"]
                and gate.inspect(trusted_anchor=worker_head)[
                    "event_count"] == 1)

            record["durable_worker_signed_evidence_after_source_recovery"] = (
                journal_verified["status"] == "SOURCE_LOCAL_COMMITTED_HOLD"
                and journal_verified["source_local_commit_verified"] is True
                and journal_verified["automatic_retry_authorized"] is False)
            fresh_rc, fresh = worker_journal_fresh_readback(journal_head)
            record["worker_journal_fresh_process_readback"] = (
                fresh_rc == 0 and fresh["status"]
                == "SOURCE_LOCAL_COMMITTED_HOLD"
                and fresh["journal_head"] == list(journal_head)
                and fresh["agent_dispatch_authorized"] is False)
            stale_rc, stale = worker_journal_fresh_readback(
                (journal_head[0]-1, journal_no_reply["journal_head"][1]))
            record["worker_journal_denies_unpinned_old_head"] = (
                stale_rc == 2 and stale["status"] == "QUARANTINED_HOLD")
            record["recovery_holds_dispatch"] = all(
                verdict[k] is False for k in (
                    "agent_dispatch_authorized",
                    "model_egress_authorized",
                    "automatic_retry_authorized",
                    "automatic_refund_authorized",
                    "lease_unfence_authorized",
                    "worker_source_distributed_atomicity_verified"))
            record["malformed_recovery_quarantined"] = (
                classify_source_recovery(
                    source_reply={"forged": True}, **recovery_scope)["status"]
                == "QUARANTINED_HOLD")
            record["wrong_pinned_recovery_quarantined"] = (
                classify_source_recovery(
                    source_reply=one, **{**recovery_scope,
                        "trusted_source_pin": "f"*64})["status"]
                    == "QUARANTINED_HOLD")
            repeat_rc, _ = remote_source(
                "commit", {
                    "operation_id": OP1, "intent_sha256": source_intent,
                    "trusted_head": list(one["head"])})
            record["repeat_commit_after_recovery_denied"] = repeat_rc == 2
            stage = "source_first_atomic_debit"
            head1 = tuple(one["head"])
            record["source_sqlite_debit_committed"] = (
                one.get("local_debit_and_signed_receipt_atomic") is True
                and one["receipt"]["payload"]["operation_id"] == OP1)
            before_source = _state_digest(2, 0, ZERO)
            after_source = _state_digest(1, 1, ZERO)
            args = {
                "source_public_key": source_pub,
                "expected_source_pin": source_pin,
                "expected_operation": OP1,
                "expected_intent_sha256": source_intent,
                "expected_head": head1,
                "expected_before_sha256": before_source,
                "expected_after_sha256": after_source,
            }
            record["source_receipt_signed_and_pinned"] = (
                verify_source_receipt(one["receipt"], **args))
            cross = {
                "worker_publication": publication,
                "worker_public_key": worker.public_key(),
                "trusted_worker_pin": worker_pin,
                "trusted_observer_pin": observer_pin,
                "expected_worker_id": "worker", "expected_observer_id": "observer",
                "expected_operation_id": OP1,
                "expected_worker_outbox_head": worker_head,
                "expected_worker_before_state": before,
                "expected_worker_after_state": after,
                "source_receipt": one["receipt"],
                "source_public_key": source_pub,
                "trusted_source_pin": source_pin,
                "expected_source_head": head1,
                "expected_source_before_state": before_source,
                "expected_source_after_state": after_source,
            }
            record["worker_intent_and_source_receipt_match"] = (
                verify_source_corresponds_to_signed_worker_intent(**cross))

            stage = "source_restart_readback"
            rc, reopened = remote_source("inspect", {"trusted_head": list(head1)})
            record["source_readback_after_process_restart"] = (
                rc == 0 and reopened.get("receipt") == one["receipt"]
                and reopened.get("balance") == 1)
            record["recovery_did_not_redebit"] = (
                rc == 0 and reopened.get("balance") == 1
                and reopened.get("head") == one["head"])
            duplicate_rc, _ = remote_source("commit", {
                "operation_id": OP1, "intent_sha256": source_intent,
                "trusted_head": list(head1)})
            record["duplicate_operation_denied"] = duplicate_rc == 2
            stale_intent = hashlib.sha256(b"original-stale-test").hexdigest()
            stale_rc, _ = remote_source("commit", {
                "operation_id": STALE_OP,
                "intent_sha256": stale_intent,
                "trusted_head": [0, ZERO]})
            record["duplicate_intent_denied_by_stale_source_head"] = stale_rc == 2
            fence_rc, fence_reply = remote_source("fence", {
                "operation_id": STALE_OP,
                "intent_sha256": stale_intent,
                "trusted_prior_head": [0, ZERO],
                "trusted_current_head": list(head1)})
            record["stale_source_attempt_signed_and_persisted"] = (
                fence_rc == 0
                and fence_reply.get("automatic_retry_authorized") is False
                and verify_stale_source_fence(
                    fence_reply.get("signed_stale_attempt_fence"),
                    source_public_key=source_pub,
                    expected_source_pin=source_pin,
                    expected_operation=STALE_OP,
                    expected_intent_sha256=stale_intent,
                    expected_prior_head=(0,ZERO),
                    expected_observed_head=head1))
            rebase_rc, _ = remote_source("commit", {
                "operation_id": STALE_OP,
                "intent_sha256": stale_intent,
                "trusted_head": list(head1)})
            record["stale_attempt_rebase_refused"] = rebase_rc == 2
            mutated_worker = deepcopy(publication)
            mutated_worker["payload"]["outbox_event"]["operation_id"] = OP2
            record["worker_publication_forgery_denied"] = (
                not verify_source_corresponds_to_signed_worker_intent(
                    **{**cross, "worker_publication": mutated_worker}))
            record["wrong_source_key_denied"] = (
                not verify_source_receipt(
                    one["receipt"], **{
                        **args, "source_public_key":
                        Ed25519PrivateKey.generate().public_key()}))
            changed = deepcopy(one["receipt"])
            changed["payload"]["intent_sha256"] = "f"*64
            record["source_receipt_mutation_denied"] = (
                not verify_source_receipt(changed, **args))

            stage = "actual_source_db_rollback"
            copy_image("source-debits.sqlite3", "old-source.sqlite3")
            rc, two = remote_source("commit", {
                "operation_id": OP2,
                "intent_sha256": hashlib.sha256(b"second-test").hexdigest(),
                "trusted_head": list(head1)})
            if rc:
                raise RuntimeError("valid second fake resource debit refused")
            head2 = tuple(two["head"])
            record["second_synthetic_debit_committed"] = (
                two.get("balance") == 0 and head2[0] == 2)
            copy_image("source-debits.sqlite3", "new-source.sqlite3")
            copy_image("old-source.sqlite3", "source-debits.sqlite3")
            rejected_rc, _ = remote_source(
                "inspect", {"trusted_head": list(head2)})
            old_rc, old_recovered = remote_source(
                "inspect", {"trusted_head": list(head1)})
            record["old_source_sqlite_rejected_by_newer_head"] = (
                rejected_rc == 2)
            record["old_source_sqlite_with_old_head_still_valid"] = (
                old_rc == 0 and old_recovered.get("balance") == 1
                and old_recovered.get("receipt") == one["receipt"])
            copy_image("new-source.sqlite3", "source-debits.sqlite3")
            resumed_rc, resumed = remote_source(
                "inspect", {"trusted_head": list(head2)})
            record["restored_new_source_head_verified"] = (
                resumed_rc == 0 and resumed.get("balance") == 0)
            exhaust_rc, _ = remote_source("commit", {
                "operation_id": "3"*32,
                "intent_sha256": hashlib.sha256(b"third-test").hexdigest(),
                "trusted_head": list(head2)})
            record["source_budget_exhaustion_denied"] = exhaust_rc == 2
            if not all(record[k] is True for k in TRUE):
                record["errors"].append("source-side physical canary incomplete")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError,
            subprocess.TimeoutExpired):
        record["errors"].append("physical source fixture denied at " + stage)
    return record


def exit_status(result: dict) -> int:
    return 0 if (
        all(result.get(k) is True for k in TRUE)
        and all(result.get(k) is False for k in FALSE)
        and type(result.get("provider_requests_forwarded")) is int
        and result["provider_requests_forwarded"] == 0
        and not result.get("errors")
    ) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--remote", default=SOURCE_HOST)
    args = parser.parse_args()
    document = physical(remote_root=args.remote_root, remote=args.remote)
    print(json.dumps(document, sort_keys=True, indent=2))
    raise SystemExit(exit_status(document))
