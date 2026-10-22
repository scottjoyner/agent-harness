#!/usr/bin/env python3
"""Synthetic Hermes quiescence with explicit pinned revocation evidence.

Research-only. No keys, trusted heads, clocks, or claims are fetched
from the worker. All outcomes are HOLD, including successful synthetic
checks. The authority signer and anchors are NOT independently custodied.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from verify_hermes_quiescent_compose import check as compose_check

DOMAIN = b"assistx-hermes-synthetic-fenced-quiescence-v1\0"
HEX = re.compile(r"^[a-f0-9]{64}$")
KIND = "synthetic_fenced_hermes_quiescence"
MAX_CLAIMS = 8
FIELDS = {
    "schema", "kind", "scope", "authority_pin", "worker_pin",
    "sequence", "previous_state_sha256", "issued_at", "expires_at",
    "revoked_claim_sha256", "claim_set_sha256", "pending_claims",
    "inflight_effects", "provider_requests", "side_effects_attested",
    "independent_custody_verified", "production_dispatch_allowed",
    "decision",
}


def canonical(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True,
                      allow_nan=False, separators=(",", ":")).encode("ascii")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(key: Ed25519PublicKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return sha(key.public_bytes(Encoding.Raw, PublicFormat.Raw))


def claimset_digest(items: list[str]) -> str:
    return sha(DOMAIN + canonical(items))


def state_digest(envelope: object) -> str:
    return sha(canonical(envelope))


def _shape(payload: object) -> bool:
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        return False
    claims = payload["revoked_claim_sha256"]
    return (
        type(payload["schema"]) is int and payload["schema"] == 1
        and payload["kind"] == KIND
        and payload["scope"] == "hermes-synthetic-no-dispatch"
        and isinstance(payload["authority_pin"], str) and HEX.fullmatch(payload["authority_pin"]) is not None
        and isinstance(payload["worker_pin"], str) and HEX.fullmatch(payload["worker_pin"]) is not None
        and payload["authority_pin"] != payload["worker_pin"]
        and type(payload["sequence"]) is int and 1 <= payload["sequence"] <= 10000
        and ((payload["sequence"] == 1) == (payload["previous_state_sha256"] == "0"*64))
        and isinstance(payload["previous_state_sha256"], str)
        and HEX.fullmatch(payload["previous_state_sha256"]) is not None
        and type(payload["issued_at"]) is int and type(payload["expires_at"]) is int
        and 0 < payload["expires_at"]-payload["issued_at"] <= 600
        and isinstance(claims, list) and len(claims) <= MAX_CLAIMS
        and all(isinstance(c, str) and HEX.fullmatch(c) for c in claims)
        and sorted(set(claims)) == claims
        and isinstance(payload["claim_set_sha256"], str)
        and HEX.fullmatch(payload["claim_set_sha256"]) is not None
        and payload["claim_set_sha256"] == claimset_digest(claims)
        and type(payload["pending_claims"]) is int and payload["pending_claims"] == 0
        and type(payload["inflight_effects"]) is int and payload["inflight_effects"] == 0
        and type(payload["provider_requests"]) is int and payload["provider_requests"] == 0
        and payload["side_effects_attested"] is False
        and payload["independent_custody_verified"] is False
        and payload["production_dispatch_allowed"] is False
        and payload["decision"] == "HOLD"
    )


def evaluate(*, signed_state: object = None,
             trusted_authority_key: Ed25519PublicKey | None = None,
             expected_authority_pin: str | None = None,
             expected_state_digest: str | None = None,
             expected_sequence: int | None = None,
             trusted_minimum_sequence: int | None = None,
             expected_previous_digest: str | None = None,
             expected_worker_pin: str | None = None,
             expected_revoked_claim_sha256: object = None,
             trusted_now: int | None = None,
             quiescence: object = None,
             compose_render: object = None,
             expected_disposable_image: str | None = None) -> dict[str, Any]:
    failures: set[str] = set()
    try:
        if (not isinstance(signed_state, dict) or set(signed_state) !=
                {"algorithm", "payload", "signature"}
                or signed_state.get("algorithm") != "Ed25519"
                or not _shape(signed_state.get("payload"))
                or not isinstance(signed_state.get("signature"), str)):
            raise ValueError("signed_revocation_missing_or_malformed")
        body = signed_state["payload"]
        if (not isinstance(trusted_authority_key, Ed25519PublicKey)
                or not isinstance(expected_authority_pin, str)
                or not HEX.fullmatch(expected_authority_pin)
                or fingerprint(trusted_authority_key) != expected_authority_pin
                or body["authority_pin"] != expected_authority_pin):
            raise ValueError("independent_authority_trust_pin_missing")
        if (not isinstance(expected_state_digest, str)
                or not HEX.fullmatch(expected_state_digest)
                or state_digest(signed_state) != expected_state_digest):
            raise ValueError("retained_external_head_mismatch")
        if (type(expected_sequence) is not int
                or type(trusted_minimum_sequence) is not int
                or not (0 <= trusted_minimum_sequence < expected_sequence)
                or expected_sequence != trusted_minimum_sequence + 1
                or body["sequence"] != expected_sequence):
            raise ValueError("stale_replayed_or_skipped_sequence")
        if (not isinstance(expected_previous_digest, str)
                or not HEX.fullmatch(expected_previous_digest)
                or body["previous_state_sha256"] != expected_previous_digest):
            raise ValueError("previous_revocation_head_mismatch")
        if (not isinstance(expected_worker_pin, str)
                or not HEX.fullmatch(expected_worker_pin)
                or body["worker_pin"] != expected_worker_pin):
            raise ValueError("worker_identity_changed")
        wanted = expected_revoked_claim_sha256
        if (not isinstance(wanted, list) or len(wanted) > MAX_CLAIMS
                or not all(isinstance(c, str) and HEX.fullmatch(c) for c in wanted)
                or sorted(set(wanted)) != wanted
                or body["revoked_claim_sha256"] != wanted):
            raise ValueError("revoked_claim_set_mismatch")
        if (type(trusted_now) is not int
                or not body["issued_at"] <= trusted_now < body["expires_at"]):
            raise ValueError("revocation_receipt_expired")
        signature = bytes.fromhex(signed_state["signature"])
        if len(signature) != 64:
            raise ValueError("signature_shape")
        trusted_authority_key.verify(signature, DOMAIN + canonical(body))
    except (InvalidSignature, ValueError, TypeError, AttributeError,
            KeyError, UnicodeError, OverflowError):
        failures.add("signed_revocation_or_retained_head_rejected")

    # Every independent gate is required, but no combination gives a
    # production token. The stub cannot attest real upstream leases.
    try:
        if not isinstance(quiescence, dict) or set(quiescence) != {
                "schema", "candidate_absent", "restart_disabled", "claims_zero",
                "inflight_effects_zero", "provider_requests_zero",
                "legacy_restore_forbidden", "scratch_intact",
        } or not (type(quiescence["schema"]) is int and quiescence["schema"] == 1
                 and all(quiescence[k] is True for k in (
                    "candidate_absent", "restart_disabled", "claims_zero",
                    "inflight_effects_zero", "provider_requests_zero",
                    "legacy_restore_forbidden", "scratch_intact"))):
            failures.add("no_dispatch_quiescence_not_proven")
    except (KeyError, TypeError):
        failures.add("no_dispatch_quiescence_not_proven")
    config = compose_check(compose_render, expected_disposable_image)
    if not config["synthetic_compose_exact_policy_pass"]:
        failures.add("quiescent_compose_not_qualified")
    return {
        "schema": 1,
        "mode": "hermes_synthetic_fenced_quiescence",
        "synthetic_evidence_contract_pass": not failures,
        "violations": sorted(failures),
        "revoked_claim_count": len(expected_revoked_claim_sha256)
            if isinstance(expected_revoked_claim_sha256, list) else None,
        "external_head_independently_custodied": False,
        "source_owner_revocation_verified": False,
        "real_inflight_effects_fenced": False,
        "production_no_dispatch_authorized": False,
        "production_rollback_authorized": False,
        "decision": "HOLD",
    }
