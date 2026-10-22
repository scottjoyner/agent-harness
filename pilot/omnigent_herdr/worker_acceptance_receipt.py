#!/usr/bin/env python3
"""Strict, research-only worker-signed synthetic transaction receipts.

These attest only to statements by one disposable worker fixture. They do
NOT establish independently controlled signing keys, a hardware root,
real source authority, or an actual model/agent dispatch authorization.
"""
from __future__ import annotations

import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from capability_gate import _decode, _encode
from observer_checkpoint import _json_bytes, public_fingerprint

DOMAIN = b"herdr-synthetic-worker-acceptance-v1\0"
HEX = re.compile(r"^[0-9a-f]{64}$")
OP = re.compile(r"^[0-9a-f]{32}$")
ROLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
COMMON = {
    "schema", "kind", "worker_id", "observer_id",
    "operation_id", "request_sha256", "prepare_sha256",
    "before_state_sha256", "after_state_sha256", "generation",
    "sequence", "receipt_number", "agent_dispatch_authorized",
    "provider_requests_forwarded",
}
FIELDS = {
    "synthetic_worker_commit": COMMON,
    "synthetic_worker_delivery": COMMON | {
        "observer_ack_sha256", "observer_checkpoint_sha256",
    },
}


def _valid_claims(payload: object, kind: str) -> bool:
    if not isinstance(payload, dict) or set(payload) != FIELDS[kind]:
        return False
    if (type(payload.get("schema")) is not int or payload["schema"] != 1
            or payload.get("kind") != kind
            or type(payload.get("generation")) is not int
            or payload["generation"] < 2
            or type(payload.get("sequence")) is not int
            or payload["sequence"] < 1
            or type(payload.get("receipt_number")) is not int
            or not 1 <= payload["receipt_number"] <= 2
            or payload.get("agent_dispatch_authorized") is not False
            or type(payload.get("provider_requests_forwarded")) is not int
            or payload["provider_requests_forwarded"] != 0):
        return False
    for role in ("worker_id", "observer_id"):
        if not isinstance(payload.get(role), str) or not ROLE.fullmatch(payload[role]):
            return False
    if payload["worker_id"] == payload["observer_id"]:
        return False
    if not isinstance(payload.get("operation_id"), str) or not OP.fullmatch(payload["operation_id"]):
        return False
    digests = (
        "request_sha256", "prepare_sha256",
        "before_state_sha256", "after_state_sha256",
    )
    if kind == "synthetic_worker_delivery":
        digests += ("observer_ack_sha256", "observer_checkpoint_sha256")
    return all(
        isinstance(payload.get(field), str) and HEX.fullmatch(payload[field])
        for field in digests
    ) and payload["before_state_sha256"] != payload["after_state_sha256"]


def sign_fixture(*, signer: Ed25519PrivateKey, kind: str,
                 fields: dict) -> dict:
    """Sign only a bounded, deny-only fixture receipt."""
    if kind not in FIELDS or not isinstance(fields, dict):
        raise ValueError("unknown worker receipt kind")
    payload = {**fields, "schema": 1, "kind": kind}
    if not _valid_claims(payload, kind):
        raise ValueError("invalid worker receipt claims")
    return {
        "algorithm": "Ed25519",
        "payload": payload,
        "signature": _encode(signer.sign(DOMAIN + _json_bytes(payload))),
    }


def verify(*, envelope: object, public_key: Ed25519PublicKey,
           trusted_fingerprint: str, expected_kind: str,
           expected: dict, observer_fingerprint: str | None = None) -> bool:
    """Strict full-scope matching. Trust-root freshness is CALLER owned."""
    if (expected_kind not in FIELDS or not isinstance(envelope, dict)
            or set(envelope) != {"algorithm", "payload", "signature"}
            or envelope.get("algorithm") != "Ed25519"
            or not _valid_claims(envelope.get("payload"), expected_kind)
            or not isinstance(trusted_fingerprint, str)
            or not HEX.fullmatch(trusted_fingerprint)
            or public_fingerprint(public_key) != trusted_fingerprint
            or observer_fingerprint == trusted_fingerprint):
        return False
    p = envelope["payload"]
    # Require the verifier to supply every claim except the fixed schema
    # and signed kind; no unchecked request/nonce/scope fields remain.
    if not isinstance(expected, dict) or set(expected) != FIELDS[expected_kind] - {"schema", "kind"}:
        return False
    if not all(type(p[field]) is type(expected[field]) and
               p[field] == expected[field] for field in expected):
        return False
    try:
        public_key.verify(
            _decode(envelope["signature"]),
            DOMAIN + _json_bytes(p))
        return True
    except (ValueError, TypeError, UnicodeError, InvalidSignature):
        return False


if __name__ == "__main__":
    raise SystemExit("Synthetic worker receipt library; no production services")
