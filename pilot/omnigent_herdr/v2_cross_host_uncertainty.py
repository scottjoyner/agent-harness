#!/usr/bin/env python3
"""Fail-closed recovery classification for a LOST synthetic source reply.

There is intentionally NO retry, refund, dispatch, or 'no effect'
decision. A cryptographically validated read-only source receipt only
proves SOURCE_LOCAL_COMMITTED_HOLD. Worker+source are NOT a distributed
atomic transaction, even when each individual SQLite commit passed.

A timeout, partition, old restored source, missing receipt, wrong
signature or substituted identity must never be interpreted as proof
that the synthetic source did not commit.
"""
from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from observer_checkpoint import ZERO
from v2_source_transaction_fixture import (
    MAX_RECORDS, SHA_RE, OP_RE, _state_digest, _digest,
    verify_source_corresponds_to_signed_worker_intent,
)

BASE = {
    "schema": 1, "mode": "synthetic_v2_cross_host_recovery_deny_only",
    "agent_dispatch_authorized": False,
    "provider_requests_forwarded": 0,
    "model_egress_authorized": False,
    "worker_source_distributed_atomicity_verified": False,
    "independent_source_custody_verified": False,
    "automatic_retry_authorized": False,
    "automatic_refund_authorized": False,
    "lease_unfence_authorized": False,
    "decision": "HOLD",
}


def _result(status: str, *, source_local_commit_verified: bool) -> dict:
    return {
        **BASE, "status": status,
        "source_local_commit_verified": source_local_commit_verified,
    }


def classify_source_recovery(
        *, source_reply: object,
        worker_publication: dict,
        worker_public_key: Ed25519PublicKey,
        trusted_worker_pin: str,
        trusted_observer_pin: str,
        expected_worker_id: str,
        expected_observer_id: str,
        expected_operation_id: str,
        expected_worker_outbox_head: tuple[int, str],
        expected_worker_before_state: str,
        expected_worker_after_state: str,
        source_public_key: Ed25519PublicKey,
        trusted_source_pin: str,
        trusted_prior_source_head: tuple[int, str]) -> dict:
    """Read-only recovery verdict; no changes to any host or database.

    A source readback after unknown COMMIT MUST be checked against the
    worker's original signed intent, pinned signer and exact previously
    trusted source head. Never infer non-commit from a missing response.
    """
    uncertain = _result("UNCERTAIN_HOLD", source_local_commit_verified=False)
    quarantined = _result("QUARANTINED_HOLD", source_local_commit_verified=False)
    if source_reply is None:
        return uncertain
    if not isinstance(source_reply, dict):
        return quarantined
    if source_reply.get("error") == "resource source denied":
        return uncertain
    keys = {
        "schema", "mode", "head", "balance", "receipt",
        "before_state_sha256", "after_state_sha256",
        "local_debit_and_signed_receipt_atomic",
        "independently_administered_source_verified",
        "source_is_real_provider", "agent_dispatch_authorized",
        "model_egress_authorized", "provider_requests_forwarded",
        "decision",
    }
    if set(source_reply) != keys:
        return quarantined
    if (type(source_reply["schema"]) is not int
            or source_reply["schema"] != 1
            or source_reply["mode"] != "synthetic_source_read_only_recovery"
            or source_reply["decision"] != "HOLD"
            or source_reply["agent_dispatch_authorized"] is not False
            or source_reply["model_egress_authorized"] is not False
            or source_reply["source_is_real_provider"] is not False
            or source_reply["independently_administered_source_verified"] is not False
            or source_reply["local_debit_and_signed_receipt_atomic"] is not True
            or type(source_reply["provider_requests_forwarded"]) is not int
            or source_reply["provider_requests_forwarded"] != 0):
        return quarantined
    prior = trusted_prior_source_head
    if (not isinstance(prior, tuple) or len(prior) != 2
            or type(prior[0]) is not int
            or not 0 <= prior[0] < MAX_RECORDS
            or not isinstance(prior[1], str)
            or SHA_RE.fullmatch(prior[1]) is None):
        return quarantined
    if (not isinstance(expected_operation_id, str)
            or OP_RE.fullmatch(expected_operation_id) is None
            or not isinstance(source_reply["head"], list)
            or len(source_reply["head"]) != 2
            or type(source_reply["head"][0]) is not int
            or source_reply["head"][0] != prior[0] + 1
            or not isinstance(source_reply["head"][1], str)
            or SHA_RE.fullmatch(source_reply["head"][1]) is None
            or type(source_reply["balance"]) is not int
            or source_reply["balance"] != MAX_RECORDS - prior[0] - 1):
        return quarantined
    receipt = source_reply["receipt"]
    if not isinstance(receipt, dict) or not isinstance(receipt.get("payload"), dict):
        return quarantined
    p = receipt["payload"]
    if (p.get("previous_record_sha256") != prior[1]
            or type(p.get("balance_before")) is not int
            or p["balance_before"] != MAX_RECORDS - prior[0]
            or type(p.get("balance_after")) is not int
            or p["balance_after"] != source_reply["balance"]
            or source_reply["head"][1] != _digest(receipt)):
        return quarantined
    before = _state_digest(MAX_RECORDS - prior[0], prior[0], prior[1])
    after = _state_digest(MAX_RECORDS - prior[0] - 1, prior[0] + 1, prior[1])
    if (source_reply["before_state_sha256"] != before
            or source_reply["after_state_sha256"] != after):
        return quarantined
    try:
        matching = verify_source_corresponds_to_signed_worker_intent(
            worker_publication=worker_publication,
            worker_public_key=worker_public_key,
            trusted_worker_pin=trusted_worker_pin,
            trusted_observer_pin=trusted_observer_pin,
            expected_worker_id=expected_worker_id,
            expected_observer_id=expected_observer_id,
            expected_operation_id=expected_operation_id,
            expected_worker_outbox_head=expected_worker_outbox_head,
            expected_worker_before_state=expected_worker_before_state,
            expected_worker_after_state=expected_worker_after_state,
            source_receipt=receipt,
            source_public_key=source_public_key,
            trusted_source_pin=trusted_source_pin,
            expected_source_head=tuple(source_reply["head"]),
            expected_source_before_state=before,
            expected_source_after_state=after)
    except (KeyError, TypeError, ValueError, AttributeError):
        matching = False
    if not matching:
        return quarantined
    return _result("SOURCE_LOCAL_COMMITTED_HOLD",
                   source_local_commit_verified=True)


if __name__ == "__main__":
    raise SystemExit("Research source recovery evidence only; always HOLD")
