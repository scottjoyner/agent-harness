#!/usr/bin/env python3
"""Read-only Ed25519 research checkpoint verifier for synthetic lease SQLite.

The checkpoint's signer key MUST be held independently of the worker. Here
the signing helper exists for disposable fixtures only; in a production
design it must be an external observer with independently custodied keys,
trusted high-water and reliably published post-transaction checkpoints.
A signature without external freshness does not prove rollback resistance.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from capability_gate import _encode, _decode
from durable_lease_store import APP_ID, SCHEMA_VERSION

DIGEST = re.compile(r"^[0-9a-f]{64}$")
LABEL = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$")
ZERO = "0" * 64
CHECKPOINT_DOMAIN = b"og-standalone-observer-checkpoint-v1\0"
REQUIRED = {
    "schema", "kind", "observer_id", "worker_id", "generation",
    "previous_checkpoint_sha256", "state_sha256", "created_unix",
}


def _json_bytes(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def checkpoint_sha256(envelope: dict) -> str:
    return hashlib.sha256(_json_bytes(envelope)).hexdigest()


def public_fingerprint(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()


def read_local_state_digest(path: Path) -> str:
    """Keyless, read-only and consistent snapshot of untrusted SQLite state.

    No anti-rollback guarantee and no independent custody. Never creates
    missing SQLite files and never uses the worker HMAC secret.
    """
    path = Path(path)
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()):
        raise ValueError("untrusted/missing state path")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("state must be a regular unlinked SQLite file")
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=3, isolation_level=None)
    try:
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("SQLite integrity check failed")
        if (db.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
            raise ValueError("wrong SQLite schema identity")
        meta = db.execute("SELECT epoch,last_clock FROM metadata WHERE id=1").fetchone()
        if meta is None or db.execute("SELECT count(*) FROM metadata").fetchone()[0] != 1:
            raise ValueError("missing/duplicated metadata")
        rows = db.execute(
            "SELECT jti,token_sha256,epoch,last_seq,used,max_receipts,revoked "
            "FROM leases ORDER BY jti"
        ).fetchall()
        data = json.dumps(
            {"epoch": meta[0], "last_clock": meta[1], "leases": rows},
            sort_keys=True, separators=(",", ":"))
        db.execute("ROLLBACK")
        return hashlib.sha256(data.encode()).hexdigest()
    finally:
        db.close()


def sign_fixture(*, observer_key: Ed25519PrivateKey,
                 observer_id: str, worker_id: str, state_sha256: str,
                 generation: int, previous_checkpoint_sha256: str,
                 created_unix: int) -> dict:
    """Sign a fixture observation; NOT a deployed independent observer."""
    if (not LABEL.fullmatch(observer_id) or not LABEL.fullmatch(worker_id)
            or observer_id == worker_id
            or not DIGEST.fullmatch(state_sha256)
            or not DIGEST.fullmatch(previous_checkpoint_sha256)
            or type(generation) is not int or generation < 1
            or type(created_unix) is not int or created_unix < 1):
        raise ValueError("invalid observer fixture record")
    if generation == 1 and previous_checkpoint_sha256 != ZERO:
        raise ValueError("genesis fixture checkpoint must have no predecessor")
    payload = {
        "schema": 1,
        "kind": "synthetic_sqlite_state_checkpoint",
        "observer_id": observer_id,
        "worker_id": worker_id,
        "generation": generation,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "state_sha256": state_sha256,
        "created_unix": created_unix,
    }
    message = CHECKPOINT_DOMAIN + _json_bytes(payload)
    return {
        "algorithm": "Ed25519",
        "payload": payload,
        "signature": _encode(observer_key.sign(message)),
    }


def verify(*, state_path: Path, envelope: object,
           observer_public_key: Ed25519PublicKey,
           trusted_observer_fingerprint: str, expected_observer_id: str,
           expected_worker_id: str, min_generation: int,
           trusted_previous_sha256: str | None = None) -> dict:
    """Fail-closed against a supplied, independently trusted checkpoint.

    min_generation and observer fingerprint MUST come from an independently
    controlled high-water/trust channel. Passing them from worker storage
    is not safe against malicious rollback.
    """
    result = {
        "schema": 1, "signature_valid": False,
        "independent_trust_root_configured": False,
        "checkpoint_generation_acceptable": False,
        "checkpoint_matches_sqlite": False,
        "physical_observer_custody_verified": False,
        "continuous_publication_verified": False,
        "rollback_resistance_verified": False,
        "agent_dispatch_authorized": False,
        "decision": "HOLD", "errors": [],
    }
    if (not isinstance(envelope, dict)
            or set(envelope) != {"algorithm", "payload", "signature"}
            or envelope.get("algorithm") != "Ed25519"
            or not isinstance(envelope.get("payload"), dict)):
        result["errors"].append("bad checkpoint envelope")
        return result
    p = envelope["payload"]
    if set(p) != REQUIRED or not (
        type(p["schema"]) is int and p["schema"] == 1
        and p["kind"] == "synthetic_sqlite_state_checkpoint"
        and isinstance(p["observer_id"], str)
        and p["observer_id"] == expected_observer_id
        and isinstance(p["worker_id"], str)
        and p["worker_id"] == expected_worker_id
        and p["observer_id"] != p["worker_id"]
        and type(p["generation"]) is int and p["generation"] >= 1
        and type(p["created_unix"]) is int and p["created_unix"] >= 1
        and isinstance(p["state_sha256"], str) and DIGEST.fullmatch(p["state_sha256"])
        and isinstance(p["previous_checkpoint_sha256"], str)
        and DIGEST.fullmatch(p["previous_checkpoint_sha256"])
        and type(min_generation) is int and min_generation >= 1
    ):
        result["errors"].append("invalid checkpoint claims or authority")
        return result
    fingerprint = public_fingerprint(observer_public_key)
    if (not isinstance(trusted_observer_fingerprint, str)
            or not DIGEST.fullmatch(trusted_observer_fingerprint)
            or fingerprint != trusted_observer_fingerprint):
        result["errors"].append("untrusted checkpoint signing key")
        return result
    # Fingerprint equality only verifies a pinned key was supplied. It
    # cannot establish independent physical key custody.
    result["independent_trust_root_configured"] = True
    try:
        signature = _decode(envelope["signature"])
        observer_public_key.verify(
            signature, CHECKPOINT_DOMAIN + _json_bytes(p))
        result["signature_valid"] = True
    except (ValueError, TypeError, InvalidSignature, UnicodeError):
        result["errors"].append("invalid checkpoint signature")
        return result
    # A correctly signed *old* checkpoint must not be substituted for the
    # externally known latest generation. Caller-supplied floors are not
    # independently validated by this module.
    result["checkpoint_generation_acceptable"] = (
        p["generation"] >= min_generation
        and (p["generation"] != 1
             or p["previous_checkpoint_sha256"] == ZERO)
        and (trusted_previous_sha256 is None
             or p["previous_checkpoint_sha256"] == trusted_previous_sha256))
    if not result["checkpoint_generation_acceptable"]:
        result["errors"].append("stale checkpoint or predecessor mismatch")
        return result
    try:
        digest = read_local_state_digest(state_path)
        result["checkpoint_matches_sqlite"] = digest == p["state_sha256"]
    except (OSError, ValueError, sqlite3.Error):
        result["errors"].append("could not read a valid SQLite snapshot")
        return result
    if not result["checkpoint_matches_sqlite"]:
        result["errors"].append("SQLite state differs from signed checkpoint")
    # Critical: a trusted public signature and state match do not prove
    # independent signer operations, reliable publication, or fresh state.
    return result


if __name__ == "__main__":
    raise SystemExit("Read-only research checkpoint library; no live signer")
