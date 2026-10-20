#!/usr/bin/env python3
"""x1 synthetic observer → Destroyer witness → Lenovo custody-sign fixture.

One-shot SSH on each node, two disposable signing secrets that remain on
their respective research hosts. No production trust policy. Pins and
checkpoint heads are all supplied by the SAME operator-controlled x1
test process, so the results prove cryptographic interoperability ONLY.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
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
from v2_durable_observer_inbox import SyntheticV2DurableObserverInbox
from v2_outbox_publication import worker_publish, worker_sign_delivery
from v2_witness_countersign import verify_witness_receipt
from v2_custody_checkpoint_contract import (
    checkpoint_digest, verify_research_checkpoint,
)

WORKER_HOST = "x1-370"
WITNESS_HOST = "destroyer"
CUSTODY_HOST = "lenovo"
DIR_WITNESS = re.compile(
    r"^/home/[^/]+/\.cache/og-v2-countersign-[A-Za-z0-9]{6}$")
DIR_CUSTODY = re.compile(
    r"^/home/[^/]+/\.cache/og-v2-custody-[A-Za-z0-9]{6}$")
WORKTREE = hashlib.sha256(b"three-node-custody-research-only").hexdigest()
OP = "3"*32
WORKER = "three-node-synthetic-worker"
OBSERVER = "three-node-synthetic-observer"
SCOPE = "herdr-omnigent.synthetic.v2"
ISSUED = 1700000000
EXPIRES = 1700000600
TRUE = (
    "four_role_keys_distinct",
    "witness_signed_genesis_verified",
    "witness_signed_publication_verified",
    "lenovo_custody_checkpoint_verified",
    "lenovo_signing_key_reopened_same_checkpoint",
    "missing_external_anchor_rejected",
    "stale_signed_checkpoint_rejected_with_newer_floor",
    "witness_receipt_mutation_rejected",
    "replaced_custodian_key_rejected",
    "generation_chain_verified",
    "old_checkpoint_with_old_floor_still_accepted",
    "expired_checkpoint_rejected",
    "lenovo_duplicate_sign_refused",
    "lenovo_durable_chain_reopened",
    "lenovo_stale_ledger_rejected_against_newer_head",
    "lenovo_old_ledger_with_old_head_still_accepted",
    "lenovo_revocation_persists_across_processes",
    "lenovo_revocation_blocks_new_valid_signature",
    "lenovo_revocation_can_be_reverted_by_same_admin",
)
FALSE = (
    "independent_custodian_verified",
    "immutable_external_highwater_verified",
    "source_transaction_attested",
    "production_rollback_resistance_verified",
    "agent_dispatch_authorized",
    "model_egress_authorized",
    "used_real_repository",
)


def physical(*, witness_root: str, custody_root: str) -> dict:
    report = {
        "schema": 1, "mode": "synthetic_three_node_v2_custody_checkpoint",
        "observer_host": socket.gethostname(),
        "witness_host": WITNESS_HOST, "custody_host": CUSTODY_HOST,
        **{k: False for k in TRUE},
        **{k: False for k in FALSE},
        "provider_requests_forwarded": 0,
        "errors": [],
    }
    if (not isinstance(witness_root, str)
            or not DIR_WITNESS.fullmatch(witness_root)
            or not isinstance(custody_root, str)
            or not DIR_CUSTODY.fullmatch(custody_root)):
        report["errors"].append("unapproved research signing roots")
        return report
    phase = "setup"
    try:
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
                dir=cache, prefix="og-v2-three-node-x1-") as temp:
            root = Path(temp)
            os.chmod(root, 0o700)
            worker = Ed25519PrivateKey.generate()
            observer = Ed25519PrivateKey.generate()
            worker_pin = public_fingerprint(worker.public_key())
            observer_pin = public_fingerprint(observer.public_key())
            observer_pub64 = base64.b64encode(
                observer.public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw)).decode("ascii")

            def remote(host: str, script: str, args: list[str],
                       message: dict) -> tuple[int, dict]:
                if (host, script) not in (
                    (WITNESS_HOST, "v2_witness_countersign.py"),
                    (CUSTODY_HOST, "v2_custody_signer_fixture.py")):
                    raise RuntimeError("unapproved remote signer")
                remotepath = (
                    witness_root if host == WITNESS_HOST else custody_root)
                cmd = [
                    "ssh", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=6", host,
                    "python3", "-B", remotepath + "/" + script,
                    "--root", remotepath, *args]
                p = subprocess.run(
                    cmd, input=json.dumps(message, sort_keys=True) + "\n",
                    text=True, capture_output=True, timeout=14,
                    check=False)
                if (p.returncode not in (0, 2)
                        or len(p.stdout) > 20*1024
                        or len(p.stderr) > 12*1024):
                    raise RuntimeError("remote signing process unavailable")
                try:
                    response = json.loads(p.stdout)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("malformed remote research result") from exc
                if (not isinstance(response, dict)
                        or response.get("decision") != "HOLD"
                        or response.get("agent_dispatch_authorized") is not False
                        or type(response.get("provider_requests_forwarded")) is not int
                        or response["provider_requests_forwarded"] != 0):
                    raise RuntimeError("unapproved remote response authority")
                return p.returncode, response

            def witness(cmd: str, *, marker=None, previous=None):
                args = [
                    "--command", cmd, "--log-name", "primary",
                    "--observer-public-base64", observer_pub64,
                    "--observer-id", OBSERVER, "--worker-id", WORKER,
                    "--worker-pin", worker_pin]
                if cmd == "bootstrap":
                    return remote(
                        WITNESS_HOST, "v2_witness_countersign.py", args, {})
                request = (
                    {"marker": marker, "trusted_anchor": previous}
                    if cmd == "append" else None)
                if request is None:
                    raise RuntimeError("witness fixture method denied")
                return remote(
                    WITNESS_HOST, "v2_witness_countersign.py", args, request)

            def custodian(cmd: str, request: dict):
                return remote(
                    CUSTODY_HOST, "v2_custody_signer_fixture.py",
                    ["--command", cmd], request)

            def copy_custody_ledger(source: str, target: str):
                # No arbitrary files or shell: only files created by this
                # one-shot fixture in the already-validated scratch root.
                permitted = {
                    "custody-checkpoints.sqlite3",
                    "ledger-old.sqlite3",
                    "ledger-current.sqlite3",
                    "ledger-unrevoked.sqlite3",
                    "ledger-revoked.sqlite3",
                }
                if source not in permitted or target not in permitted:
                    raise RuntimeError("unapproved synthetic ledger image")
                command = [
                    "ssh", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=6", CUSTODY_HOST,
                    "cp", "-f", "--",
                    custody_root + "/" + source,
                    custody_root + "/" + target,
                ]
                checked = subprocess.run(
                    command, capture_output=True, text=True, timeout=12)
                if checked.returncode != 0:
                    raise RuntimeError("disposable Lenovo image copy failed")

            phase = "role_bootstrap"
            rc, wb = witness("bootstrap")
            if rc or wb.get("schema") != 1:
                raise RuntimeError("witness bootstrap refused")
            wbytes = base64.b64decode(
                wb["witness_public_base64"], validate=True)
            if len(wbytes) != 32:
                raise RuntimeError("bad witness signer pin")
            witness_pub = Ed25519PublicKey.from_public_bytes(wbytes)
            witness_pin = public_fingerprint(witness_pub)
            if witness_pin != wb["witness_fingerprint"]:
                raise RuntimeError("bootstrap witness fingerprint mismatch")
            rc, cb = custodian("bootstrap", {})
            if rc or cb.get("schema") != 1:
                raise RuntimeError("custodian bootstrap refused")
            cbytes = base64.b64decode(
                cb["custodian_public_base64"], validate=True)
            if len(cbytes) != 32:
                raise RuntimeError("bad research custodian signer pin")
            custodian_pub = Ed25519PublicKey.from_public_bytes(cbytes)
            custodian_pin = public_fingerprint(custodian_pub)
            if custodian_pin != cb["custodian_fingerprint"]:
                raise RuntimeError("research custodian fingerprint mismatch")
            report["four_role_keys_distinct"] = len({
                worker_pin, observer_pin, witness_pin, custodian_pin}) == 4
            if not report["four_role_keys_distinct"]:
                raise RuntimeError("physical trust roles overlap")

            gate = AtomicSyntheticLeaseOutbox(
                root / "worker.sqlite3", secrets.token_bytes(32),
                create=True, now=lambda: 1000)
            token = gate.mint(
                uid=os.getuid(), owner="fixture-owner",
                session="fixture-session",
                worktree_sha256=WORKTREE,
                model_pin="kilo/kilo-auto/free")
            before = gate.state_digest()
            result = gate.check(
                {"schema": 1, "op": "synthetic_receipt",
                 "token": token, "seq": 1},
                operation_id=OP, peer_uid=os.getuid(),
                expected_owner="fixture-owner",
                expected_session="fixture-session",
                expected_worktree_sha256=WORKTREE,
                expected_model_pin="kilo/kilo-auto/free")
            after = gate.state_digest()
            outbox_head = (
                result["atomic_outbox_record"],
                result["atomic_outbox_sha256"])
            publication = worker_publish(
                signer=worker, worker_id=WORKER,
                observer_id=OBSERVER, observer_fingerprint=observer_pin,
                exported_head=gate.export_head_event(
                    trusted_anchor=outbox_head))
            dbpath = root / "observer.sqlite3"
            inbox = SyntheticV2DurableObserverInbox(
                dbpath, observer_signer=observer,
                worker_public_key=worker.public_key(),
                observer_id=OBSERVER, worker_id=WORKER,
                initial_state=before, create=True)
            def reopen():
                return SyntheticV2DurableObserverInbox(
                    dbpath, observer_signer=observer,
                    worker_public_key=worker.public_key(),
                    observer_id=OBSERVER, worker_id=WORKER)
            head0 = inbox.created_head
            def stage(mark, prev):
                rc, reply = witness("append", marker=mark, previous=prev)
                if rc:
                    raise RuntimeError("valid witness append denied")
                wh = tuple(reply["witness_head"])
                oh = tuple(reply["observer_head"])
                if not verify_witness_receipt(
                        reply["signed_receipt"],
                        trusted_witness_key=witness_pub,
                        expected_witness_pin=witness_pin,
                        observer_public_key=observer.public_key(),
                        worker_fingerprint=worker_pin,
                        observer_id=OBSERVER, worker_id=WORKER,
                        expected_witness_head=wh,
                        expected_observer_head=oh):
                    raise RuntimeError("unsigned/forged witness readback")
                return wh, reply["signed_receipt"]
            phase = "signed_genesis"
            w0, receipt0 = stage(
                reopen().export_signed_marker(trusted_anchor=head0), None)
            report["witness_signed_genesis_verified"] = (
                w0[0] == 1
                and receipt0["payload"]["observer_revision"] == 0)

            phase = "signed_publication"
            head1 = reopen().receive(
                publication=publication, trusted_anchor=head0,
                expected_operation_id=OP,
                expected_outbox_head=outbox_head,
                expected_after_state=after)
            w1, receipt1 = stage(
                reopen().export_signed_marker(trusted_anchor=head1), w0)
            report["witness_signed_publication_verified"] = (
                w1[0] == 2 and receipt1["payload"]["observer_revision"] == 1)

            def custodian_request(receipt, wh, oh, *, generation, previous):
                return {
                    "witness_receipt": receipt,
                    "witness_public_base64": wb["witness_public_base64"],
                    "witness_pin": witness_pin,
                    "observer_public_base64": observer_pub64,
                    "worker_fingerprint": worker_pin,
                    "observer_id": OBSERVER, "worker_id": WORKER,
                    "witness_head": wh,
                    "observer_head": oh,
                    "scope": SCOPE,
                    "generation": generation,
                    "previous_checkpoint_sha256": previous,
                    "issued_at": ISSUED, "expires_at": EXPIRES,
                }

            def verify(envelope, receipt, wh, oh, *,
                       generation, previous, expected_digest, floor):
                return verify_research_checkpoint(
                    envelope,
                    trusted_custodian_key=custodian_pub,
                    expected_custodian_pin=custodian_pin,
                    witness_receipt=receipt,
                    trusted_witness_key=witness_pub,
                    expected_witness_pin=witness_pin,
                    observer_public_key=observer.public_key(),
                    worker_fingerprint=worker_pin,
                    observer_id=OBSERVER, worker_id=WORKER,
                    expected_witness_head=wh,
                    expected_observer_head=oh,
                    expected_scope=SCOPE,
                    expected_generation=generation,
                    expected_previous_checkpoint_sha256=previous,
                    expected_checkpoint_sha256=expected_digest,
                    trusted_now=ISSUED + 10,
                    minimum_witness_count=floor)

            phase = "custody_checkpoint_1"
            request1 = custodian_request(
                receipt1, w1, head1,
                generation=1, previous=ZERO)
            rc, cp1_response = custodian("sign", request1)
            if rc:
                raise RuntimeError("valid research checkpoint 1 denied")
            cp1 = cp1_response["checkpoint"]
            cp1_digest = cp1_response["checkpoint_sha256"]
            report["lenovo_custody_checkpoint_verified"] = (
                verify(cp1, receipt1, w1, head1, generation=1,
                       previous=ZERO, expected_digest=cp1_digest,
                       floor=0))
            # A lost response must use a SEPARATE read-only API. The
            # original signing command is permanently non-idempotent.
            rc_repeat, _denied = custodian("sign", request1)
            rc, replay = custodian("readback", {
                "generation": 1, "witness_head": list(w1),
                "observer_head": list(head1), "scope": SCOPE,
            })
            report["lenovo_signing_key_reopened_same_checkpoint"] = (
                rc_repeat == 2 and rc == 0
                and replay.get("checkpoint") == cp1
                and replay.get("checkpoint_sha256") == cp1_digest)
            report["missing_external_anchor_rejected"] = (
                not verify(cp1, receipt1, w1, head1,
                           generation=1, previous=ZERO,
                           expected_digest=None, floor=0))
            report["witness_receipt_mutation_rejected"] = False
            changed = deepcopy(receipt1)
            changed["payload"]["observer_stage"] = "delivered"
            rc, _ = custodian(
                "sign", {**request1, "witness_receipt": changed})
            report["witness_receipt_mutation_rejected"] = rc == 2
            report["replaced_custodian_key_rejected"] = (
                not verify_research_checkpoint(
                    cp1,
                    trusted_custodian_key=Ed25519PrivateKey.generate().public_key(),
                    expected_custodian_pin=custodian_pin,
                    witness_receipt=receipt1,
                    trusted_witness_key=witness_pub,
                    expected_witness_pin=witness_pin,
                    observer_public_key=observer.public_key(),
                    worker_fingerprint=worker_pin,
                    observer_id=OBSERVER, worker_id=WORKER,
                    expected_witness_head=w1, expected_observer_head=head1,
                    expected_scope=SCOPE, expected_generation=1,
                    expected_previous_checkpoint_sha256=ZERO,
                    expected_checkpoint_sha256=cp1_digest,
                    trusted_now=ISSUED + 10, minimum_witness_count=0))
            report["expired_checkpoint_rejected"] = (
                not verify_research_checkpoint(
                    cp1, trusted_custodian_key=custodian_pub,
                    expected_custodian_pin=custodian_pin,
                    witness_receipt=receipt1,
                    trusted_witness_key=witness_pub,
                    expected_witness_pin=witness_pin,
                    observer_public_key=observer.public_key(),
                    worker_fingerprint=worker_pin,
                    observer_id=OBSERVER, worker_id=WORKER,
                    expected_witness_head=w1, expected_observer_head=head1,
                    expected_scope=SCOPE, expected_generation=1,
                    expected_previous_checkpoint_sha256=ZERO,
                    expected_checkpoint_sha256=cp1_digest,
                    trusted_now=EXPIRES, minimum_witness_count=0))

            phase = "custody_checkpoint_2"
            head2, ack = reopen().issue_ack(trusted_anchor=head1)
            w2, receipt2 = stage(
                reopen().export_signed_marker(trusted_anchor=head2), w1)
            request2 = custodian_request(
                receipt2, w2, head2,
                generation=2, previous=cp1_digest)
            # Preserve one valid older image for an actual on-Lenovo
            # SQLite rollback injection, never from user/production data.
            copy_custody_ledger(
                "custody-checkpoints.sqlite3", "ledger-old.sqlite3")
            rc, cp2_response = custodian("sign", request2)
            if rc:
                raise RuntimeError("valid checkpoint 2 denied")
            cp2_digest = cp2_response["checkpoint_sha256"]
            report["generation_chain_verified"] = verify(
                cp2_response["checkpoint"], receipt2, w2, head2,
                generation=2, previous=cp1_digest,
                expected_digest=cp2_digest, floor=w1[0])
            report["stale_signed_checkpoint_rejected_with_newer_floor"] = (
                not verify(cp1, receipt1, w1, head1,
                           generation=1, previous=ZERO,
                           expected_digest=cp1_digest, floor=w1[0])
                and not verify(
                    cp1, receipt1, w1, head1, generation=2,
                    previous=cp1_digest, expected_digest=cp2_digest,
                    floor=w1[0]))
            report["old_checkpoint_with_old_floor_still_accepted"] = (
                verify(cp1, receipt1, w1, head1, generation=1,
                       previous=ZERO, expected_digest=cp1_digest, floor=0))

            phase = "durable_ledger_duplicate_and_restart"
            duplicate_rc, _ = custodian("sign", request2)
            recover_rc, recovered = custodian("readback", {
                "generation": 2, "witness_head": list(w2),
                "observer_head": list(head2), "scope": SCOPE,
            })
            report["lenovo_duplicate_sign_refused"] = duplicate_rc == 2
            report["lenovo_durable_chain_reopened"] = (
                recover_rc == 0
                and recovered.get("checkpoint_sha256") == cp2_digest
                and recovered.get("checkpoint") == cp2_response["checkpoint"]
                and recovered.get("ledger_generation") == 2)

            phase = "actual_ledger_image_rollback"
            copy_custody_ledger(
                "custody-checkpoints.sqlite3", "ledger-current.sqlite3")
            copy_custody_ledger(
                "ledger-old.sqlite3", "custody-checkpoints.sqlite3")
            new_head_rc, _ = custodian("readback", {
                "generation": 2, "witness_head": list(w2),
                "observer_head": list(head2), "scope": SCOPE,
            })
            old_head_rc, old_readback = custodian("readback", {
                "generation": 1, "witness_head": list(w1),
                "observer_head": list(head1), "scope": SCOPE,
            })
            report["lenovo_stale_ledger_rejected_against_newer_head"] = (
                new_head_rc == 2)
            report["lenovo_old_ledger_with_old_head_still_accepted"] = (
                old_head_rc == 0
                and old_readback.get("checkpoint_sha256") == cp1_digest)
            copy_custody_ledger(
                "ledger-current.sqlite3", "custody-checkpoints.sqlite3")
            resumed_rc, resumed = custodian("readback", {
                "generation": 2, "witness_head": list(w2),
                "observer_head": list(head2), "scope": SCOPE,
            })
            if resumed_rc != 0 or resumed.get("checkpoint_sha256") != cp2_digest:
                raise RuntimeError("newest research custody ledger failed to recover")

            phase = "local_revocation"
            copy_custody_ledger(
                "custody-checkpoints.sqlite3", "ledger-unrevoked.sqlite3")
            revoke_rc, revoked = custodian("revoke", {
                "generation": 2, "checkpoint_sha256": cp2_digest})
            recovered_after_rc, _ = custodian("readback", {
                "generation": 2, "witness_head": list(w2),
                "observer_head": list(head2), "scope": SCOPE,
            })
            report["lenovo_revocation_persists_across_processes"] = (
                revoke_rc == 0 and revoked.get("locally_revoked") is True
                and recovered_after_rc == 2)

            # A valid NEW observer/witness stage must remain un-signable
            # when this local custodian has been revoked.
            delivery = worker_sign_delivery(
                signer=worker, publication=publication,
                observer_ack=ack,
                observer_public_key=observer.public_key(),
                trusted_observer_fingerprint=observer_pin,
                expected_generation=2,
                previous_checkpoint_sha256=ZERO)
            head3 = reopen().confirm(
                delivery=delivery, trusted_anchor=head2)
            w3, receipt3 = stage(
                reopen().export_signed_marker(trusted_anchor=head3), w2)
            request3 = custodian_request(
                receipt3, w3, head3, generation=3,
                previous=cp2_digest)
            denied_rc, _ = custodian("sign", request3)
            report["lenovo_revocation_blocks_new_valid_signature"] = (
                denied_rc == 2)
            # Required negative control: operator can restore old valid
            # ledger and old claimed trust root to undo the revocation.
            phase = "revocation_rollback_negative_control"
            copy_custody_ledger(
                "custody-checkpoints.sqlite3", "ledger-revoked.sqlite3")
            copy_custody_ledger(
                "ledger-unrevoked.sqlite3", "custody-checkpoints.sqlite3")
            old_recover_rc, old_recover = custodian("readback", {
                "generation": 2, "witness_head": list(w2),
                "observer_head": list(head2), "scope": SCOPE,
            })
            report["lenovo_revocation_can_be_reverted_by_same_admin"] = (
                old_recover_rc == 0
                and old_recover.get("checkpoint_sha256") == cp2_digest)
            copy_custody_ledger(
                "ledger-revoked.sqlite3", "custody-checkpoints.sqlite3")
            final_denied_rc, _ = custodian("readback", {
                "generation": 2, "witness_head": list(w2),
                "observer_head": list(head2), "scope": SCOPE,
            })
            if final_denied_rc != 2:
                raise RuntimeError("revocation fence not restored after control")

            if not all(report[k] is True for k in TRUE):
                report["errors"].append("incomplete 3-node custody experiment")
    except (OSError, ValueError, RuntimeError, TypeError, KeyError,
            UnicodeError, subprocess.TimeoutExpired):
        report["errors"].append("3-node research fixture denied at " + phase)
    return report


def exit_status(report: dict) -> int:
    return 0 if (
        all(report.get(k) is True for k in TRUE)
        and all(report.get(k) is False for k in FALSE)
        and type(report.get("provider_requests_forwarded")) is int
        and report["provider_requests_forwarded"] == 0
        and not report.get("errors")
    ) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--witness-root", required=True)
    parser.add_argument("--custody-root", required=True)
    args = parser.parse_args()
    doc = physical(witness_root=args.witness_root,
                   custody_root=args.custody_root)
    print(json.dumps(doc, indent=2, sort_keys=True))
    raise SystemExit(exit_status(doc))
