#!/usr/bin/env python3
"""RESEARCH-ONLY offline fourth-role custody checkpoint contract.

Requires a separately supplied, pinned custodian Ed25519 public key
and exact, externally held checkpoint SHA-256 and previous high-water.
A valid signature proves only control of that signing key: it cannot
prove the custodian is independent, that a real transaction took place,
or that the externally supplied head is tamper-resistant.

No model, provider, agent, release-authorizing, or network operations.
"""
from __future__ import annotations

import hashlib
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from capability_gate import _decode, _encode
from observer_checkpoint import ZERO, _json_bytes, public_fingerprint
from v2_witness_countersign import verify_witness_receipt

DOMAIN = b"herdr-synthetic-v2-external-custody-checkpoint-v1\0"
SHA = re.compile(r"^[0-9a-f]{64}$")
SCOPE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,95}$")
FIELDS = {
    "schema", "kind", "scope", "generation", "previous_checkpoint_sha256",
    "custodian_fingerprint", "witness_fingerprint",
    "observer_fingerprint", "worker_fingerprint",
    "witness_record_count", "witness_head_sha256",
    "observer_revision", "observer_marker_sha256",
    "witness_receipt_sha256", "issued_at", "expires_at",
    "source_transaction_attested", "independent_custody_verified",
    "agent_dispatch_authorized", "provider_requests_forwarded",
    "decision",
}


class UnsafeCustodyCheckpoint(ValueError):
    """An absent, stale, forged or ambiguous research trust assertion."""


def _sha(x: object) -> bool:
    return isinstance(x, str) and SHA.fullmatch(x) is not None


def checkpoint_digest(envelope: dict) -> str:
    return hashlib.sha256(_json_bytes(envelope)).hexdigest()


def _payload_valid(p: object) -> bool:
    if not isinstance(p, dict) or set(p) != FIELDS:
        return False
    if (type(p["schema"]) is not int or p["schema"] != 1
            or p["kind"] != "synthetic_v2_external_custody_checkpoint"
            or not isinstance(p["scope"], str) or
            SCOPE.fullmatch(p["scope"]) is None
            or type(p["generation"]) is not int
            or not 1 <= p["generation"] <= 4
            or not all(_sha(p[k]) for k in (
                "previous_checkpoint_sha256", "custodian_fingerprint",
                "witness_fingerprint", "observer_fingerprint",
                "worker_fingerprint", "witness_head_sha256",
                "observer_marker_sha256", "witness_receipt_sha256"))
            or len({p["custodian_fingerprint"], p["witness_fingerprint"],
                    p["observer_fingerprint"], p["worker_fingerprint"]}) != 4
            or type(p["witness_record_count"]) is not int
            or not 1 <= p["witness_record_count"] <= 4
            or type(p["observer_revision"]) is not int
            or p["observer_revision"] not in range(5)
            or type(p["issued_at"]) is not int
            or type(p["expires_at"]) is not int
            or not 0 < p["expires_at"] - p["issued_at"] <= 3600
            or p["source_transaction_attested"] is not False
            or p["independent_custody_verified"] is not False
            or p["agent_dispatch_authorized"] is not False
            or type(p["provider_requests_forwarded"]) is not int
            or p["provider_requests_forwarded"] != 0
            or p["decision"] != "HOLD"):
        return False
    if (p["generation"] == 1) != (
            p["previous_checkpoint_sha256"] == ZERO):
        return False
    return True


def _validated_receipt(*, witness_receipt: dict,
                       trusted_witness_key: Ed25519PublicKey,
                       expected_witness_pin: str,
                       observer_public_key: Ed25519PublicKey,
                       worker_fingerprint: str,
                       observer_id: str, worker_id: str,
                       expected_witness_head: tuple[int, str],
                       expected_observer_head: tuple[int, str]) -> None:
    if not verify_witness_receipt(
            witness_receipt,
            trusted_witness_key=trusted_witness_key,
            expected_witness_pin=expected_witness_pin,
            observer_public_key=observer_public_key,
            worker_fingerprint=worker_fingerprint,
            observer_id=observer_id, worker_id=worker_id,
            expected_witness_head=expected_witness_head,
            expected_observer_head=expected_observer_head):
        raise UnsafeCustodyCheckpoint("untrusted witness receipt")


def sign_research_checkpoint(
        *, custodian_signer: Ed25519PrivateKey,
        witness_receipt: dict,
        trusted_witness_key: Ed25519PublicKey,
        expected_witness_pin: str,
        observer_public_key: Ed25519PublicKey,
        worker_fingerprint: str,
        observer_id: str, worker_id: str,
        expected_witness_head: tuple[int, str],
        expected_observer_head: tuple[int, str],
        scope: str, generation: int,
        previous_checkpoint_sha256: str,
        issued_at: int, expires_at: int) -> dict:
    """Make *only* synthetic HOLD evidence; no authority grant."""
    _validated_receipt(
        witness_receipt=witness_receipt,
        trusted_witness_key=trusted_witness_key,
        expected_witness_pin=expected_witness_pin,
        observer_public_key=observer_public_key,
        worker_fingerprint=worker_fingerprint,
        observer_id=observer_id, worker_id=worker_id,
        expected_witness_head=expected_witness_head,
        expected_observer_head=expected_observer_head)
    payload = {
        "schema": 1, "kind": "synthetic_v2_external_custody_checkpoint",
        "scope": scope, "generation": generation,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "custodian_fingerprint": public_fingerprint(
            custodian_signer.public_key()),
        "witness_fingerprint": expected_witness_pin,
        "observer_fingerprint": public_fingerprint(observer_public_key),
        "worker_fingerprint": worker_fingerprint,
        "witness_record_count": expected_witness_head[0],
        "witness_head_sha256": expected_witness_head[1],
        "observer_revision": expected_observer_head[0],
        "observer_marker_sha256": expected_observer_head[1],
        "witness_receipt_sha256": checkpoint_digest(witness_receipt),
        "issued_at": issued_at, "expires_at": expires_at,
        "source_transaction_attested": False,
        "independent_custody_verified": False,
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0, "decision": "HOLD",
    }
    if not _payload_valid(payload):
        raise UnsafeCustodyCheckpoint("invalid synthetic custody scope or trust roles")
    return {
        "algorithm": "Ed25519",
        "payload": payload,
        "signature": _encode(
            custodian_signer.sign(DOMAIN + _json_bytes(payload))),
    }


def verify_research_checkpoint(
        envelope: object, *,
        trusted_custodian_key: Ed25519PublicKey,
        expected_custodian_pin: str,
        witness_receipt: dict,
        trusted_witness_key: Ed25519PublicKey,
        expected_witness_pin: str,
        observer_public_key: Ed25519PublicKey,
        worker_fingerprint: str,
        observer_id: str, worker_id: str,
        expected_witness_head: tuple[int, str],
        expected_observer_head: tuple[int, str],
        expected_scope: str, expected_generation: int,
        expected_previous_checkpoint_sha256: str,
        expected_checkpoint_sha256: str,
        trusted_now: int, minimum_witness_count: int) -> bool:
    """Fail closed unless independently *supplied* pins & digest match.

    Caller-held trust values are required arguments; never derive them
    from the supplied checkpoint. Their actual external custody cannot
    be established in this research-only code path.
    """
    try:
        if (not isinstance(envelope, dict)
                or set(envelope) != {"algorithm", "payload", "signature"}
                or envelope.get("algorithm") != "Ed25519"
                or not _payload_valid(envelope.get("payload"))
                or not _sha(expected_custodian_pin)
                or not _sha(expected_checkpoint_sha256)
                or not _sha(expected_previous_checkpoint_sha256)
                or public_fingerprint(trusted_custodian_key)
                   != expected_custodian_pin
                or checkpoint_digest(envelope)
                   != expected_checkpoint_sha256
                or type(expected_generation) is not int
                or type(trusted_now) is not int
                or type(minimum_witness_count) is not int
                or minimum_witness_count < 0
                or not isinstance(expected_witness_head, tuple)
                or len(expected_witness_head) != 2
                or not isinstance(expected_observer_head, tuple)
                or len(expected_observer_head) != 2):
            return False
        p = envelope["payload"]
        if (p["custodian_fingerprint"] != expected_custodian_pin
                or p["witness_fingerprint"] != expected_witness_pin
                or p["observer_fingerprint"]
                   != public_fingerprint(observer_public_key)
                or p["worker_fingerprint"] != worker_fingerprint
                or p["scope"] != expected_scope
                or p["generation"] != expected_generation
                or p["previous_checkpoint_sha256"]
                   != expected_previous_checkpoint_sha256
                or p["witness_receipt_sha256"]
                   != checkpoint_digest(witness_receipt)
                or (p["witness_record_count"], p["witness_head_sha256"])
                   != expected_witness_head
                or (p["observer_revision"], p["observer_marker_sha256"])
                   != expected_observer_head
                or p["witness_record_count"] <= minimum_witness_count
                or not p["issued_at"] <= trusted_now < p["expires_at"]):
            return False
        _validated_receipt(
            witness_receipt=witness_receipt,
            trusted_witness_key=trusted_witness_key,
            expected_witness_pin=expected_witness_pin,
            observer_public_key=observer_public_key,
            worker_fingerprint=worker_fingerprint,
            observer_id=observer_id, worker_id=worker_id,
            expected_witness_head=expected_witness_head,
            expected_observer_head=expected_observer_head)
        trusted_custodian_key.verify(
            _decode(envelope["signature"]),
            DOMAIN + _json_bytes(p))
        return True
    except (UnsafeCustodyCheckpoint, InvalidSignature,
            ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        return False


if __name__ == "__main__":
    raise SystemExit("Offline V2 research verifier only: no admission API")
