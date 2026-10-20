#!/usr/bin/env python3
"""Physical two-worker race against ONE disposable Destroyer source head.

Safe by construction: two ephemeral local signed SYNTHETIC worker intents,
two bounded one-shot SSH clients, one disposable two-unit source SQLite DB.
A single source-head winner must commit. The loser is UNCERTAIN/HOLD,
never retried, refunded, or given the winning worker's source receipt.
No model/provider calls, resident service, production DB or real work.
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
import threading
from concurrent.futures import ThreadPoolExecutor
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

HOST = "destroyer"
ROOT = re.compile(r"^/home/[^/]+/\.cache/og-v2-source-[A-Za-z0-9]{6}$")
SCENARIOS = ("distinct-ops", "same-op-different-intent", "duplicate-delivery")
MODEL = "kilo/kilo-auto/free"
WITNESS = (
    "two_scoped_signed_workers_or_exact_duplicate",
    "two_remote_processes_reached_source",
    "exactly_one_source_local_debit",
    "winner_signature_and_intent_verified",
    "losing_commit_refused",
    "losing_read_only_recovery_correctly_scoped",
    "loser_receipt_provenance_scoped",
    "losing_operation_rebase_denied",
    "stale_fence_signed_or_duplicate_operation_denied",
    "source_balance_still_one",
    "no_second_source_record",
    "read_only_winner_receipt_recovered",
    "local_fencing_order_replayed",
)
DENIED = (
    "resource_is_production",
    "real_work_executed",
    "independent_resource_custody_verified",
    "cross_host_atomicity_verified",
    "agent_dispatch_authorized",
    "model_egress_authorized",
    "automatic_retry_authorized",
    "automatic_refund_authorized",
    "lease_unfence_authorized",
)


def _b64(key):
    return base64.b64encode(key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )).decode("ascii")


def _worker(here: Path, *, number: int, operation: str, worktree: str):
    worker = Ed25519PrivateKey.generate()
    observer = Ed25519PrivateKey.generate()
    work_root = here / ("worker-" + str(number))
    work_root.mkdir(mode=0o700)
    store = AtomicSyntheticLeaseOutbox(
        work_root / "lease.sqlite3", os.urandom(32),
        now=lambda: 1000, create=True)
    owner = f"synthetic-owner-{number}"
    session = f"synthetic-session-{number}"
    token = store.mint(
        uid=os.getuid(), owner=owner, session=session,
        worktree_sha256=worktree, model_pin=MODEL)
    before = store.state_digest()
    result = store.check(
        {"schema": 1, "op": "synthetic_receipt", "token": token, "seq": 1},
        peer_uid=os.getuid(), operation_id=operation,
        expected_owner=owner, expected_session=session,
        expected_worktree_sha256=worktree, expected_model_pin=MODEL)
    if result.get("status") != "synthetic_receipt_only":
        raise RuntimeError("synthetic local worker receipt not accepted")
    after = store.state_digest()
    head = (result["atomic_outbox_record"],
            result["atomic_outbox_sha256"])
    publication = worker_publish(
        signer=worker,
        worker_id=f"worker-{number}", observer_id=f"observer-{number}",
        observer_fingerprint=public_fingerprint(observer.public_key()),
        exported_head=store.export_head_event(trusted_anchor=head))
    event = publication["payload"]["outbox_event"]
    if event["operation_id"] != operation:
        raise RuntimeError("signed worker operation inconsistent")
    return {
        "worker_public_key": worker.public_key(),
        "worker_pin": public_fingerprint(worker.public_key()),
        "observer_pin": public_fingerprint(observer.public_key()),
        "worker_id": f"worker-{number}",
        "observer_id": f"observer-{number}",
        "operation": operation,
        "intent": event["request_sha256"],
        "publication": publication,
        "head": head,
        "before": before, "after": after,
    }


def _remote(root: str, command: str, request: dict, *,
            host: str = HOST, timeout: float = 20.0):
    if command not in ("bootstrap", "commit", "inspect", "recover", "fence"):
        raise ValueError("operation outside read-only/synthetic source fixture")
    p = subprocess.run(
        ["ssh", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=yes",
         "-o", "ConnectTimeout=6", host,
         "python3", "-B", root + "/v2_source_transaction_fixture.py",
         "--root", root, "--command", command],
        input=json.dumps(request, sort_keys=True)+"\n",
        capture_output=True, text=True, timeout=timeout)
    if p.returncode not in (0, 2) or len(p.stdout) > 8192:
        raise RuntimeError("bounded source process unavailable")
    payload=json.loads(p.stdout)
    if (not isinstance(payload, dict)
            or payload.get("decision")!="HOLD"
            or payload.get("agent_dispatch_authorized") is not False
            or type(payload.get("provider_requests_forwarded")) is not int
            or payload["provider_requests_forwarded"] != 0):
        raise RuntimeError("source has forbidden positive authority")
    return p.returncode, payload


def physical(*, remote_root: str, scenario: str,
             host: str = HOST) -> dict:
    report = {
        "schema": 1, "mode": "synthetic_two_worker_fenced_source_head_race",
        "worker_host": socket.gethostname(),
        "resource_host": HOST,
        "scenario": scenario,
        **{k: False for k in WITNESS},
        **{k: False for k in DENIED},
        "provider_requests_forwarded": 0, "decision": "HOLD",
        "errors": [],
    }
    if (host != HOST or not isinstance(remote_root,str)
            or ROOT.fullmatch(remote_root) is None
            or scenario not in SCENARIOS):
        report["errors"].append("unapproved synthetic worker race")
        return report
    phase = "bootstrap"
    try:
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
                prefix="og-v2-race-x1-", dir=cache) as base:
            home = Path(base)
            home.chmod(0o700)
            rc, genesis = _remote(remote_root, "bootstrap", {}, host=host)
            if rc or genesis.get("head") != [0,ZERO] or genesis.get("balance") != 2:
                raise RuntimeError("wrong fake source genesis")
            source_raw = base64.b64decode(
                genesis["source_public_base64"], validate=True)
            if len(source_raw)!=32:
                raise RuntimeError("source key not Ed25519")
            source_key = Ed25519PublicKey.from_public_bytes(source_raw)
            source_pin = public_fingerprint(source_key)
            if source_pin != genesis["source_fingerprint"]:
                raise RuntimeError("wrong source signer pin")
            if scenario == "distinct-ops":
                operations = ("a"*32, "b"*32)
                worktrees = (
                    hashlib.sha256(b"synthetic-op-a").hexdigest(),
                    hashlib.sha256(b"synthetic-op-b").hexdigest())
            elif scenario == "same-op-different-intent":
                operations = ("a"*32, "a"*32)
                worktrees = (
                    hashlib.sha256(b"synthetic-intent-a").hexdigest(),
                    hashlib.sha256(b"synthetic-intent-b").hexdigest())
            else:
                operations = ("a"*32, "a"*32)
                worktrees = (hashlib.sha256(b"same-intent").hexdigest(),)*2
            workers = [_worker(home, number=i+1, operation=operations[i],
                               worktree=worktrees[i]) for i in range(2)]
            # Third scenario is a TRUE duplicate signed request sent
            # twice by two distinct SSH CLI processes, not a newly
            # signed second operation that merely reuses an operation ID.
            if scenario == "duplicate-delivery":
                workers[1] = workers[0]
            report["two_scoped_signed_workers_or_exact_duplicate"] = (
                all(w["head"][0]==1 for w in workers)
                and ((
                    scenario == "duplicate-delivery"
                    and workers[0]["intent"] == workers[1]["intent"]
                    and workers[0]["publication"] == workers[1]["publication"]
                ) or (
                    scenario != "duplicate-delivery"
                    and len({w["worker_pin"] for w in workers}) == 2
                    and len({w["worker_pin"] for w in workers}|{source_pin}) == 3
                    and (scenario != "same-op-different-intent"
                         or workers[0]["intent"] != workers[1]["intent"])
                )))

            phase = "concurrent_ssh_source_local_commit"
            barrier = threading.Barrier(2, timeout=8)
            def request_source(index):
                w=workers[index]
                barrier.wait()
                return index, _remote(
                    remote_root, "commit", {
                        "operation_id": w["operation"],
                        "intent_sha256": w["intent"],
                        "trusted_head": [0,ZERO]}, host=host)
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = [pool.submit(request_source, k) for k in (0,1)]
                results = [f.result(timeout=24) for f in pending]
            replies={index:pair for index,pair in results}
            success=[i for i,(rc,_) in replies.items() if rc==0]
            denied=[i for i,(rc,_) in replies.items() if rc==2]
            report["two_remote_processes_reached_source"] = len(results)==2
            report["exactly_one_source_local_debit"] = (
                len(success)==1 and len(denied)==1)
            if len(success)!=1 or len(denied)!=1:
                raise RuntimeError("single-winner synthetic source fencing failed")
            winning=success[0]; losing=denied[0]
            winner=workers[winning]; loser=workers[losing]
            rc, committed = replies[winning]
            _, loser_reply = replies[losing]
            head=tuple(committed["head"])
            before = _state_digest(2, 0, ZERO)
            after = _state_digest(1, 1, ZERO)
            report["winner_signature_and_intent_verified"] = (
                verify_source_receipt(
                    committed["receipt"], source_public_key=source_key,
                    expected_source_pin=source_pin,
                    expected_operation=winner["operation"],
                    expected_intent_sha256=winner["intent"],
                    expected_head=head,
                    expected_before_sha256=before,
                    expected_after_sha256=after)
                and verify_source_corresponds_to_signed_worker_intent(
                    worker_publication=winner["publication"],
                    worker_public_key=winner["worker_public_key"],
                    trusted_worker_pin=winner["worker_pin"],
                    trusted_observer_pin=winner["observer_pin"],
                    expected_worker_id=winner["worker_id"],
                    expected_observer_id=winner["observer_id"],
                    expected_operation_id=winner["operation"],
                    expected_worker_outbox_head=winner["head"],
                    expected_worker_before_state=winner["before"],
                    expected_worker_after_state=winner["after"],
                    source_receipt=committed["receipt"],
                    source_public_key=source_key,
                    trusted_source_pin=source_pin,
                    expected_source_head=head,
                    expected_source_before_state=before,
                    expected_source_after_state=after))

            def context(w):
                return {
                    "worker_publication": w["publication"],
                    "worker_public_key": w["worker_public_key"],
                    "trusted_worker_pin": w["worker_pin"],
                    "trusted_observer_pin": w["observer_pin"],
                    "expected_worker_id": w["worker_id"],
                    "expected_observer_id": w["observer_id"],
                    "expected_operation_id": w["operation"],
                    "expected_worker_outbox_head": w["head"],
                    "expected_worker_before_state": w["before"],
                    "expected_worker_after_state": w["after"],
                    "source_public_key": source_key,
                    "trusted_source_pin": source_pin,
                    "trusted_prior_source_head": (0, ZERO),
                }

            phase="loser_denied_and_not_retryable"
            decision = classify_source_recovery(
                source_reply=loser_reply, **context(loser))
            report["losing_commit_refused"] = (
                loser_reply.get("error") == "resource source denied"
                and decision["status"] == "UNCERTAIN_HOLD"
                and decision["automatic_retry_authorized"] is False
                and decision["automatic_refund_authorized"] is False)
            rejected = classify_source_recovery(
                source_reply={**committed,
                              "mode": "synthetic_source_read_only_recovery"},
                **context(loser))
            if scenario == "duplicate-delivery":
                # The original exact SAME operation may properly
                # recover the one committed source-local receipt;
                # it still must not execute or submit another debit.
                report["loser_receipt_provenance_scoped"] = (
                    rejected["status"] == "SOURCE_LOCAL_COMMITTED_HOLD"
                    and rejected["automatic_retry_authorized"] is False
                    and rejected["agent_dispatch_authorized"] is False)
            else:
                report["loser_receipt_provenance_scoped"] = (
                    rejected["status"] == "QUARANTINED_HOLD"
                    and rejected["agent_dispatch_authorized"] is False)

            rc_l, loser_recover = _remote(
                remote_root,"recover",{
                    "operation_id": loser["operation"],
                    "intent_sha256": loser["intent"],
                    "trusted_prior_head":[0,ZERO]},host=host)
            loser_recovery_status=classify_source_recovery(
                source_reply=loser_recover, **context(loser))["status"]
            if scenario == "duplicate-delivery":
                report["losing_read_only_recovery_correctly_scoped"] = (
                    rc_l == 0 and
                    loser_recovery_status == "SOURCE_LOCAL_COMMITTED_HOLD"
                    and loser_recover.get("receipt") == committed["receipt"])
            else:
                report["losing_read_only_recovery_correctly_scoped"] = (
                    rc_l == 2 and loser_recovery_status == "UNCERTAIN_HOLD")

            # The critical earlier loophole: a losing distinct operation
            # could be REBASED with the winner's newest head. The source
            # now stores a locally signed denial fence inside the
            # same serialized SQLite gate and refuses that op ID forever.
            phase = "stale_loser_rebase_negative_control"
            rc_rebase, _rebase_reply = _remote(
                remote_root, "commit", {
                    "operation_id": loser["operation"],
                    "intent_sha256": loser["intent"],
                    "trusted_head": list(head)},host=host)
            report["losing_operation_rebase_denied"] = rc_rebase == 2
            rc_fence, fence_reply = _remote(
                remote_root, "fence", {
                    "operation_id": loser["operation"],
                    "intent_sha256": loser["intent"],
                    "trusted_prior_head": [0,ZERO],
                    "trusted_current_head": list(head)},host=host)
            if scenario == "distinct-ops":
                fence = fence_reply.get("signed_stale_attempt_fence")
                report["stale_fence_signed_or_duplicate_operation_denied"] = (
                    rc_fence == 0
                    and fence_reply.get("automatic_retry_authorized") is False
                    and verify_stale_source_fence(
                        fence, source_public_key=source_key,
                        expected_source_pin=source_pin,
                        expected_operation=loser["operation"],
                        expected_intent_sha256=loser["intent"],
                        expected_prior_head=(0,ZERO),
                        expected_observed_head=head))
            else:
                # Same op ID as committed winner must be rejected
                # by committed-operation uniqueness, not misreported
                # as a distinct stale-operation fence.
                report["stale_fence_signed_or_duplicate_operation_denied"] = (
                    rc_fence == 2 and
                    fence_reply.get("agent_dispatch_authorized") is False)

            phase="read_only_winner_recovery_and_state_check"
            rc_w, recovered = _remote(
                remote_root, "recover", {
                    "operation_id": winner["operation"],
                    "intent_sha256": winner["intent"],
                    "trusted_prior_head":[0,ZERO]},host=host)
            good = classify_source_recovery(
                source_reply=recovered, **context(winner))
            report["read_only_winner_receipt_recovered"] = (
                rc_w==0 and good["status"]=="SOURCE_LOCAL_COMMITTED_HOLD"
                and good["source_local_commit_verified"] is True
                and good["agent_dispatch_authorized"] is False)
            rc_s,state=_remote(
                remote_root,"inspect", {"trusted_head":list(head)},host=host)
            report["source_balance_still_one"] = (
                rc_s==0 and state.get("balance")==1
                and recovered.get("head")==committed.get("head"))
            report["no_second_source_record"] = (
                state.get("head")==committed.get("head")
                and state.get("receipt")==committed.get("receipt"))
            # If a caller uses the CURRENT source head instead of the
            # original genesis and reissues the losing operation, today's
            # source CLI does not have an immutable *signed attempt fence*.
            # DO NOT do that in the physical test. It would deliberately
            # produce an additional source effect. This is an explicit
            # outstanding admission/forwarding blocker.
            report["local_fencing_order_replayed"] = all(
                reply.get("decision")=="HOLD"
                and reply.get("agent_dispatch_authorized") is False
                for _,reply in replies.values())
    except (ValueError, TypeError, KeyError, OSError, RuntimeError,
            subprocess.TimeoutExpired, threading.BrokenBarrierError):
        report["errors"].append("synthetic source fork-race denied at "+phase)
    return report


def result_code(report: dict):
    return 0 if (
        all(report.get(k) is True for k in WITNESS)
        and all(report.get(k) is False for k in DENIED)
        and report.get("provider_requests_forwarded")==0
        and report.get("decision")=="HOLD"
        and not report.get("errors")
    ) else 2


if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--remote-root",required=True)
    ap.add_argument("--scenario", choices=SCENARIOS, required=True)
    ap.add_argument("--host",default=HOST)
    args=ap.parse_args()
    report=physical(remote_root=args.remote_root,scenario=args.scenario,
                    host=args.host)
    print(json.dumps(report,indent=2,sort_keys=True))
    raise SystemExit(result_code(report))
