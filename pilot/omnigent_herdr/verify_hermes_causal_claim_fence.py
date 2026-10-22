#!/usr/bin/env python3
"""Research-only signed link from source-owner census to later revocation.

The source census and revocation list could have identical claim hashes
while describing DIFFERENT issuance epochs. This verifier requires a
separately signed causal link tying both exact envelopes together.
All trust anchors are injected, NOT independently held; ALWAYS HOLD.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import verify_hermes_source_owner_census as source
import verify_hermes_fenced_quiescence as fence

DOMAIN = b"assistx-hermes-synthetic-owner-revocation-causal-link-v1\0"
KIND = "synthetic_owner_revocation_causal_binding"
HEX = re.compile(r"^[a-f0-9]{64}$")
FIELDS = {
    "schema", "kind", "scope", "revocation_authority_pin", "worker_pin",
    "source_census_sha256", "source_census_sequence",
    "revocation_sha256", "revocation_sequence",
    "sequence", "previous_binding_sha256", "issued_at", "expires_at",
    "resource_effects_fenced", "independent_custody_verified",
    "production_dispatch_authorized", "decision",
}
SOURCE_ARGS = {
    "census", "trusted_source_owner_key", "expected_owner_pin",
    "expected_census_digest", "expected_census_sequence",
    "retained_census_minimum", "expected_census_previous",
    "trusted_now", "fence_args",
}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _hex(value: object) -> bool:
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _shape(binding: object) -> bool:
    if not isinstance(binding, dict) or set(binding) != FIELDS:
        return False
    s=binding
    return (
        type(s["schema"]) is int and s["schema"]==1
        and s["kind"]==KIND and s["scope"]=="hermes-synthetic-owner-revocation"
        and all(_hex(s[k]) for k in (
            "revocation_authority_pin","worker_pin","source_census_sha256",
            "revocation_sha256","previous_binding_sha256"))
        and all(type(s[k]) is int and 1 <= s[k] <= 10000 for k in (
            "source_census_sequence","revocation_sequence","sequence"))
        and ((s["sequence"]==1)==(s["previous_binding_sha256"]=="0"*64))
        and type(s["issued_at"]) is int and type(s["expires_at"]) is int
        and 0 < s["expires_at"]-s["issued_at"] <= 600
        and s["resource_effects_fenced"] is False
        and s["independent_custody_verified"] is False
        and s["production_dispatch_authorized"] is False
        and s["decision"]=="HOLD"
    )


def check(*, source_args: object=None, signed_binding: object=None,
          expected_binding_digest: object=None,
          expected_binding_sequence: object=None,
          trusted_binding_minimum: object=None,
          expected_previous_binding_digest: object=None,
          trusted_now: object=None) -> dict[str, Any]:
    errors: set[str] = set()
    owner_and_revocation_ok = False
    binding_ok = False
    if (not isinstance(source_args, dict) or set(source_args) != SOURCE_ARGS
            or type(trusted_now) is not int
            or source_args.get("trusted_now") != trusted_now
            or not isinstance(source_args.get("fence_args"), dict)
            or source_args["fence_args"].get("trusted_now") != trusted_now):
        errors.add("source_and_revocation_inputs_untrusted")
    else:
        try:
            result=source.check_source_owned_quiescence(**source_args)
            owner_and_revocation_ok = result["synthetic_evidence_contract_pass"] is True
        except (ValueError, TypeError, KeyError, AttributeError):
            owner_and_revocation_ok=False
        if not owner_and_revocation_ok:
            errors.add("signed_owner_claims_or_revocation_denied")

    if owner_and_revocation_ok:
        try:
            if (not isinstance(signed_binding, dict)
                    or set(signed_binding)!={"algorithm","payload","signature"}
                    or signed_binding["algorithm"]!="Ed25519"
                    or not _shape(signed_binding["payload"])):
                raise ValueError("untrusted_binding_shape")
            b=signed_binding["payload"]
            census=source_args["census"]["payload"]
            revoked=source_args["fence_args"]["signed_state"]["payload"]
            signing_key=source_args["fence_args"]["trusted_authority_key"]
            expected_signer=source_args["fence_args"]["expected_authority_pin"]
            if (not isinstance(signing_key, Ed25519PublicKey)
                    or not _hex(expected_signer)
                    or fence.fingerprint(signing_key)!=expected_signer
                    or b["revocation_authority_pin"]!=expected_signer
                    or b["worker_pin"]!=census["worker_pin"]
                    or b["worker_pin"]!=revoked["worker_pin"]
                    or b["source_census_sha256"]!=source.digest(source_args["census"])
                    or b["source_census_sha256"]!=source_args["expected_census_digest"]
                    or b["source_census_sequence"]!=census["sequence"]
                    or b["revocation_sha256"]!=fence.state_digest(
                        source_args["fence_args"]["signed_state"])
                    or b["revocation_sha256"]!=source_args["fence_args"]["expected_state_digest"]
                    or b["revocation_sequence"]!=revoked["sequence"]):
                raise ValueError("binding_does_not_reference_exact_signed_inputs")
            if (not (type(expected_binding_sequence) is int
                    and type(trusted_binding_minimum) is int
                    and expected_binding_sequence==trusted_binding_minimum+1
                    and 0 <= trusted_binding_minimum < expected_binding_sequence
                    and b["sequence"]==expected_binding_sequence
                    and _hex(expected_previous_binding_digest)
                    and b["previous_binding_sha256"]==expected_previous_binding_digest
                    and _hex(expected_binding_digest)
                    and digest(signed_binding)==expected_binding_digest)):
                raise ValueError("binding_head_or_sequence_mismatch")
            # Ordering is only as trustworthy as signer-issued timestamps.
            if (not (census["issued_at"] < revoked["issued_at"] <= b["issued_at"]
                    <= trusted_now < min(census["expires_at"],
                                         revoked["expires_at"],b["expires_at"]))):
                raise ValueError("revocation_not_causally_after_source_snapshot")
            signature=bytes.fromhex(signed_binding["signature"])
            if len(signature)!=64:
                raise ValueError("bad_signature_size")
            signing_key.verify(signature,DOMAIN+canonical(b))
            binding_ok=True
        except (InvalidSignature, ValueError, TypeError, KeyError,
                AttributeError, UnicodeError, OverflowError):
            errors.add("signed_causal_binding_denied")
    return {
        "schema": 1,
        "mode": "hermes_synthetic_source_census_revocation_causal_fence",
        "synthetic_source_claims_verified": owner_and_revocation_ok,
        "synthetic_causal_link_verified": binding_ok,
        "synthetic_evidence_contract_pass": owner_and_revocation_ok and binding_ok,
        "violations": sorted(errors),
        "signer_timestamp_independent": False,
        "external_head_independently_custodied": False,
        "source_owner_independently_administered": False,
        "resource_effects_fenced": False,
        "production_no_dispatch_authorized": False,
        "production_rollback_authorized": False,
        "decision": "HOLD",
    }
