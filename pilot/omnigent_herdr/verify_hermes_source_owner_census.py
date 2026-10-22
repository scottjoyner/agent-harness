#!/usr/bin/env python3
"""Synthetic source-owner claim census + Hermes signed revocation gate.

The source-owned census is a separately signed, separately pinned list
of active claim SHA256 identifiers. This does NOT create a real source
authority or custody: every trust pin, head and time comes from caller.
No task, provider, production storage or real API is accessed.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import verify_hermes_fenced_quiescence as fence

DOMAIN = b"assistx-hermes-synthetic-source-owner-census-v1\0"
HEX = re.compile(r"^[a-f0-9]{64}$")
MAX_CLAIMS = 8
FIELDS = {
    "schema", "kind", "scope", "source_owner_pin", "worker_pin",
    "sequence", "previous_sha256", "issued_at", "expires_at",
    "active_claim_sha256", "claim_set_sha256",
    "resource_side_effects_attested", "independent_custody_verified",
    "source_owner_admission_authorized", "decision",
}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def fingerprint(key: Ed25519PublicKey) -> str:
    return hashlib.sha256(key.public_bytes(Encoding.Raw, PublicFormat.Raw)).hexdigest()


def claim_digest(claims: list[str]) -> str:
    return hashlib.sha256(DOMAIN + canonical(claims)).hexdigest()


def _claim_list(value: object) -> bool:
    return (
        isinstance(value, list) and len(value) <= MAX_CLAIMS
        and all(isinstance(c, str) and HEX.fullmatch(c) is not None for c in value)
        and sorted(set(value)) == value
    )


def _payload_valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != FIELDS:
        return False
    claims = value["active_claim_sha256"]
    return (
        type(value["schema"]) is int and value["schema"] == 1
        and value["kind"] == "synthetic_source_owner_claim_census"
        and value["scope"] == "hermes-synthetic-owner-claims"
        and isinstance(value["source_owner_pin"], str)
        and HEX.fullmatch(value["source_owner_pin"]) is not None
        and isinstance(value["worker_pin"], str)
        and HEX.fullmatch(value["worker_pin"]) is not None
        and value["source_owner_pin"] != value["worker_pin"]
        and type(value["sequence"]) is int and 1 <= value["sequence"] <= 10000
        and isinstance(value["previous_sha256"], str)
        and HEX.fullmatch(value["previous_sha256"]) is not None
        and ((value["sequence"] == 1) == (value["previous_sha256"] == "0"*64))
        and type(value["issued_at"]) is int
        and type(value["expires_at"]) is int
        and 0 < value["expires_at"] - value["issued_at"] <= 600
        and _claim_list(claims)
        and value["claim_set_sha256"] == claim_digest(claims)
        and value["resource_side_effects_attested"] is False
        and value["independent_custody_verified"] is False
        and value["source_owner_admission_authorized"] is False
        and value["decision"] == "HOLD"
    )


def verify_census(
    envelope: object, *, trusted_source_owner_key: object,
    expected_owner_pin: object, expected_census_digest: object,
    expected_sequence: object, retained_minimum_sequence: object,
    expected_previous_digest: object, expected_worker_pin: object,
    trusted_now: object, expected_revocation_authority_pin: object,
) -> bool:
    """Require independent caller-supplied trust pins; no self-pinning."""
    try:
        if not isinstance(envelope, dict) or set(envelope) != {
                "algorithm", "payload", "signature"}:
            return False
        if envelope["algorithm"] != "Ed25519" or not _payload_valid(envelope["payload"]):
            return False
        body = envelope["payload"]
        if (not isinstance(trusted_source_owner_key, Ed25519PublicKey)
                or not isinstance(expected_owner_pin, str)
                or HEX.fullmatch(expected_owner_pin) is None
                or fingerprint(trusted_source_owner_key) != expected_owner_pin
                or body["source_owner_pin"] != expected_owner_pin
                or expected_owner_pin == expected_revocation_authority_pin
                or body["worker_pin"] != expected_worker_pin):
            return False
        if (not isinstance(expected_census_digest, str)
                or not HEX.fullmatch(expected_census_digest)
                or digest(envelope) != expected_census_digest
                or not isinstance(expected_previous_digest, str)
                or not HEX.fullmatch(expected_previous_digest)
                or body["previous_sha256"] != expected_previous_digest):
            return False
        if (type(expected_sequence) is not int
                or type(retained_minimum_sequence) is not int
                or not 0 <= retained_minimum_sequence < expected_sequence
                or expected_sequence != retained_minimum_sequence + 1
                or body["sequence"] != expected_sequence
                or type(trusted_now) is not int
                or not body["issued_at"] <= trusted_now < body["expires_at"]):
            return False
        signature = bytes.fromhex(envelope["signature"])
        if len(signature) != 64:
            return False
        trusted_source_owner_key.verify(signature, DOMAIN + canonical(body))
        return True
    except (InvalidSignature, ValueError, TypeError, KeyError,
            AttributeError, UnicodeError, OverflowError):
        return False


def check_source_owned_quiescence(
    *, census: object = None, trusted_source_owner_key: object = None,
    expected_owner_pin: object = None, expected_census_digest: object = None,
    expected_census_sequence: object = None,
    retained_census_minimum: object = None,
    expected_census_previous: object = None,
    trusted_now: object = None, fence_args: object = None,
) -> dict[str, Any]:
    """No worker-provided claim-list parameter: derive list from signed census."""
    valid = False
    revocation_qualified = False
    if isinstance(fence_args, dict):
        # Only exact verifier params; caller must not sneak another claim
        # list, signer or policy override under an unexpected keyword.
        expected = {
            "signed_state", "trusted_authority_key",
            "expected_authority_pin", "expected_state_digest",
            "expected_sequence", "trusted_minimum_sequence",
            "expected_previous_digest", "expected_worker_pin",
            "trusted_now", "quiescence", "compose_render",
            "expected_disposable_image",
        }
        if set(fence_args) == expected and isinstance(census, dict):
            valid = verify_census(
                census, trusted_source_owner_key=trusted_source_owner_key,
                expected_owner_pin=expected_owner_pin,
                expected_census_digest=expected_census_digest,
                expected_sequence=expected_census_sequence,
                retained_minimum_sequence=retained_census_minimum,
                expected_previous_digest=expected_census_previous,
                expected_worker_pin=fence_args["expected_worker_pin"],
                expected_revocation_authority_pin=fence_args["expected_authority_pin"],
                trusted_now=trusted_now,
            )
            # Verify source-owner list FIRST; do not ask the fence to
            # operate if owner source is unauthenticated or stale.
            if valid:
                params = dict(fence_args)
                params["expected_revoked_claim_sha256"] = census["payload"]["active_claim_sha256"]
                params["trusted_now"] = trusted_now
                try:
                    revocation_qualified = fence.evaluate(**params)["synthetic_evidence_contract_pass"]
                except (TypeError, ValueError, KeyError, AttributeError):
                    revocation_qualified = False
    return {
        "schema": 1, "mode": "hermes_synthetic_source_claim_census_and_revocation",
        "synthetic_owner_census_verified": valid,
        "synthetic_all_censused_claims_revoked": valid and revocation_qualified,
        "synthetic_evidence_contract_pass": valid and revocation_qualified,
        "violations": (
            ([] if valid else ["untrusted_or_stale_source_owner_claim_census"])
            + ([] if valid and revocation_qualified else ["revocation_or_quiescence_incomplete"])
        ),
        "source_owner_truly_independent": False,
        "external_head_independently_custodied": False,
        "resource_side_effects_attested": False,
        "production_no_dispatch_authorized": False,
        "production_rollback_authorized": False,
        "decision": "HOLD",
    }
