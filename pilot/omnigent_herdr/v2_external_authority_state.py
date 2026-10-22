#!/usr/bin/env python3
"""Offline synthetic outside-custody policy state and revocation verifier.

This module NEVER decides to execute an agent. It checks whether a
checkpoint remains consistent with an explicitly supplied signed external
state and separately pinned authority key/high-water. The authority
signature proves possession of the synthetic key ONLY: the caller must
provide its trust pin, current exact digest and epoch/sequence floor from
some OTHER, genuinely protected source for real rollback resistance.

Both the signer and the external trust state are synthetic test fixtures.
"""
from __future__ import annotations

import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from capability_gate import _decode, _encode
from observer_checkpoint import ZERO, _json_bytes, public_fingerprint
from v2_custody_checkpoint_contract import (
    SHA, SCOPE, checkpoint_digest, verify_research_checkpoint,
)

DOMAIN = b"herdr-synthetic-v2-external-policy-state-v1\0"
FIELDS = {
    "schema", "kind", "scope", "authority_pin", "custodian_pin",
    "witness_pin", "observer_pin", "worker_pin", "sequence",
    "previous_state_sha256", "checkpoint_generation",
    "checkpoint_sha256", "witness_count", "state", "issued_at",
    "expires_at", "independent_custody_verified",
    "source_transaction_attested", "agent_dispatch_authorized",
    "provider_requests_forwarded", "decision",
}
STATES = ("checkpoint_recorded", "checkpoint_revoked")


class UnsafeExternalAuthority(ValueError):
    """Unknown signer, stale state, ambiguous revocation, or bad policy."""


def _sha(value: object) -> bool:
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def digest(envelope: object) -> str:
    return hashlib.sha256(_json_bytes(envelope)).hexdigest()


def _shape(p: object) -> bool:
    if not isinstance(p, dict) or set(p) != FIELDS:
        return False
    if (type(p["schema"]) is not int or p["schema"] != 1
            or p["kind"] != "synthetic_v2_external_policy_state"
            or not isinstance(p["scope"], str)
            or SCOPE.fullmatch(p["scope"]) is None
            or not all(_sha(p[k]) for k in (
                "authority_pin", "custodian_pin", "witness_pin",
                "observer_pin", "worker_pin", "checkpoint_sha256",
                "previous_state_sha256"))
            or len({
                p["authority_pin"], p["custodian_pin"], p["witness_pin"],
                p["observer_pin"], p["worker_pin"]}) != 5
            or type(p["sequence"]) is not int
            or not 1 <= p["sequence"] <= 16
            or type(p["checkpoint_generation"]) is not int
            or not 1 <= p["checkpoint_generation"] <= 4
            or type(p["witness_count"]) is not int
            or not 1 <= p["witness_count"] <= 4
            or p["state"] not in STATES
            or type(p["issued_at"]) is not int
            or type(p["expires_at"]) is not int
            or not 0 < p["expires_at"]-p["issued_at"] <= 3600
            or p["independent_custody_verified"] is not False
            or p["source_transaction_attested"] is not False
            or p["agent_dispatch_authorized"] is not False
            or type(p["provider_requests_forwarded"]) is not int
            or p["provider_requests_forwarded"] != 0
            or p["decision"] != "HOLD"):
        return False
    if (p["sequence"] == 1) != (p["previous_state_sha256"] == ZERO):
        return False
    return True


def sign_synthetic_state(
        *, authority_signer: Ed25519PrivateKey,
        scope: str, custodian_pin: str, witness_pin: str,
        observer_pin: str, worker_pin: str,
        sequence: int, previous_state_sha256: str,
        checkpoint_generation: int, checkpoint_sha256: str,
        witness_count: int, state: str,
        issued_at: int, expires_at: int) -> dict:
    """Only fake off-fleet state; callable for disposable evidence tests."""
    p = {
        "schema": 1,
        "kind": "synthetic_v2_external_policy_state",
        "scope": scope, "authority_pin":
            public_fingerprint(authority_signer.public_key()),
        "custodian_pin": custodian_pin,
        "witness_pin": witness_pin,
        "observer_pin": observer_pin,
        "worker_pin": worker_pin,
        "sequence": sequence,
        "previous_state_sha256": previous_state_sha256,
        "checkpoint_generation": checkpoint_generation,
        "checkpoint_sha256": checkpoint_sha256,
        "witness_count": witness_count, "state": state,
        "issued_at": issued_at, "expires_at": expires_at,
        "independent_custody_verified": False,
        "source_transaction_attested": False,
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0, "decision": "HOLD",
    }
    if not _shape(p):
        raise UnsafeExternalAuthority("invalid synthetic external state")
    return {
        "algorithm": "Ed25519", "payload": p,
        "signature": _encode(
            authority_signer.sign(DOMAIN + _json_bytes(p))),
    }


def verify_external_state(
        state: object, *,
        trusted_authority_key: Ed25519PublicKey,
        expected_authority_pin: str, expected_state_sha256: str,
        expected_sequence: int, expected_previous_sha256: str,
        minimum_sequence: int, expected_scope: str,
        expected_custodian_pin: str, expected_witness_pin: str,
        expected_observer_pin: str, expected_worker_pin: str,
        trusted_now: int) -> bool:
    """Validate pinned external evidence. Never derive a pin from state."""
    try:
        if (not isinstance(state, dict)
                or set(state) != {"algorithm", "payload", "signature"}
                or state["algorithm"] != "Ed25519"
                or not _shape(state.get("payload"))
                or not _sha(expected_authority_pin)
                or not _sha(expected_state_sha256)
                or not _sha(expected_previous_sha256)
                or type(expected_sequence) is not int
                or type(minimum_sequence) is not int
                or type(trusted_now) is not int
                or minimum_sequence < 0
                or public_fingerprint(trusted_authority_key)
                   != expected_authority_pin
                or digest(state) != expected_state_sha256):
            return False
        p = state["payload"]
        if (p["authority_pin"] != expected_authority_pin
                or p["scope"] != expected_scope
                or p["custodian_pin"] != expected_custodian_pin
                or p["witness_pin"] != expected_witness_pin
                or p["observer_pin"] != expected_observer_pin
                or p["worker_pin"] != expected_worker_pin
                or p["sequence"] != expected_sequence
                or p["sequence"] <= minimum_sequence
                or p["previous_state_sha256"] != expected_previous_sha256
                or not p["issued_at"] <= trusted_now < p["expires_at"]):
            return False
        trusted_authority_key.verify(
            _decode(state["signature"]), DOMAIN + _json_bytes(p))
        return True
    except (InvalidSignature, ValueError, TypeError, AttributeError,
            KeyError, UnicodeError):
        return False


def check_checkpoint_evidence_only(
        *, authority_state: object,
        trusted_authority_key: Ed25519PublicKey,
        expected_authority_pin: str,
        expected_state_sha256: str,
        expected_sequence: int, expected_previous_sha256: str,
        minimum_sequence: int,
        checkpoint: dict, witness_receipt: dict,
        trusted_custodian_key: Ed25519PublicKey,
        trusted_witness_key: Ed25519PublicKey,
        observer_public_key: Ed25519PublicKey,
        worker_fingerprint: str, observer_id: str, worker_id: str,
        expected_witness_head: tuple[int, str],
        expected_observer_head: tuple[int, str],
        expected_scope: str, expected_checkpoint_generation: int,
        expected_checkpoint_previous_sha256: str,
        expected_checkpoint_sha256: str,
        minimum_witness_count: int, trusted_now: int) -> bool:
    """True means only that the synthetic EVIDENCE is consistent, never
    that execution/agent model admission is allowed.
    A signed revocation state always makes this False.
    """
    try:
        custodian_pin = public_fingerprint(trusted_custodian_key)
        witness_pin = public_fingerprint(trusted_witness_key)
        observer_pin = public_fingerprint(observer_public_key)
        if not verify_external_state(
                authority_state, trusted_authority_key=trusted_authority_key,
                expected_authority_pin=expected_authority_pin,
                expected_state_sha256=expected_state_sha256,
                expected_sequence=expected_sequence,
                expected_previous_sha256=expected_previous_sha256,
                minimum_sequence=minimum_sequence,
                expected_scope=expected_scope,
                expected_custodian_pin=custodian_pin,
                expected_witness_pin=witness_pin,
                expected_observer_pin=observer_pin,
                expected_worker_pin=worker_fingerprint,
                trusted_now=trusted_now):
            return False
        p = authority_state["payload"]
        if (p["state"] != "checkpoint_recorded"
                or p["checkpoint_sha256"] != expected_checkpoint_sha256
                or p["checkpoint_generation"]
                   != expected_checkpoint_generation
                or p["witness_count"] != expected_witness_head[0]):
            return False
        return verify_research_checkpoint(
            checkpoint, trusted_custodian_key=trusted_custodian_key,
            expected_custodian_pin=custodian_pin,
            witness_receipt=witness_receipt,
            trusted_witness_key=trusted_witness_key,
            expected_witness_pin=witness_pin,
            observer_public_key=observer_public_key,
            worker_fingerprint=worker_fingerprint,
            observer_id=observer_id, worker_id=worker_id,
            expected_witness_head=expected_witness_head,
            expected_observer_head=expected_observer_head,
            expected_scope=expected_scope,
            expected_generation=expected_checkpoint_generation,
            expected_previous_checkpoint_sha256=
                expected_checkpoint_previous_sha256,
            expected_checkpoint_sha256=expected_checkpoint_sha256,
            minimum_witness_count=minimum_witness_count,
            trusted_now=trusted_now)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def verify_revocation_evidence_only(
        *, authority_state: object, trusted_authority_key: Ed25519PublicKey,
        expected_authority_pin: str, expected_state_sha256: str,
        expected_sequence: int, expected_previous_sha256: str,
        minimum_sequence: int, expected_scope: str,
        expected_custodian_pin: str, expected_witness_pin: str,
        expected_observer_pin: str, expected_worker_pin: str,
        expected_checkpoint_sha256: str, trusted_now: int) -> bool:
    """Only proves a PINNED signer stated synthetic revocation. No grant."""
    if not verify_external_state(
            authority_state, trusted_authority_key=trusted_authority_key,
            expected_authority_pin=expected_authority_pin,
            expected_state_sha256=expected_state_sha256,
            expected_sequence=expected_sequence,
            expected_previous_sha256=expected_previous_sha256,
            minimum_sequence=minimum_sequence,
            expected_scope=expected_scope,
            expected_custodian_pin=expected_custodian_pin,
            expected_witness_pin=expected_witness_pin,
            expected_observer_pin=expected_observer_pin,
            expected_worker_pin=expected_worker_pin,
            trusted_now=trusted_now):
        return False
    p = authority_state["payload"]
    return (p["state"] == "checkpoint_revoked"
            and p["checkpoint_sha256"] == expected_checkpoint_sha256)


if __name__ == "__main__":
    raise SystemExit("Synthetic offline external-authority fixture ONLY; HOLD")
