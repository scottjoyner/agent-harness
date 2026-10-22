#!/usr/bin/env python3
"""Ephemeral off-node SYNTHETIC checkpoint signer; NEVER a trust custodian.

Designed ONLY for bounded Fleet Commander x1/Destroyer/Lenovo research.
One-time key is confined to 0700 $HOME/.cache/og-v2-custody-XXXXXX,
destroyed on exit. The same fleet operator controls its SSH bootstrap,
keys, and caller-provided receipt and pin values. Does not prove
independent authority, true resource effects or immutable head custody.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sqlite3
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from observer_checkpoint import public_fingerprint
from v2_custody_checkpoint_contract import (
    UnsafeCustodyCheckpoint, checkpoint_digest, sign_research_checkpoint,
)

ROOT_NAME = re.compile(r"^og-v2-custody-[A-Za-z0-9]{6}$")
KEY_NAME = "custodian-ephemeral.key"
MAX_REQUEST = 16 * 1024


class UnsafeResearchCustodian(ValueError):
    """A non-fixture path, request or private signing-key inode."""


def _root_ok(root: Path) -> None:
    if (not root.is_absolute()
            or root.parent != Path.home() / ".cache"
            or ROOT_NAME.fullmatch(root.name) is None
            or not root.is_dir() or root.is_symlink()):
        raise UnsafeResearchCustodian("unapproved temporary custodian root")
    s = root.stat()
    if s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise UnsafeResearchCustodian("custody directory not private")


def _load_key(root: Path, *, create: bool) -> Ed25519PrivateKey:
    _root_ok(root)
    path = root / KEY_NAME
    if create:
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600)
        try:
            if os.write(fd, raw) != 32:
                raise UnsafeResearchCustodian("short fixture key write")
            os.fsync(fd)
        finally:
            os.close(fd)
        parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return key
    try:
        fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise UnsafeResearchCustodian("missing ephemeral signer key") from exc
    try:
        s = os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1
                or s.st_uid != os.getuid() or s.st_mode & 0o077
                or s.st_size != 32):
            raise UnsafeResearchCustodian("not a safe ephemeral signer key")
        raw = os.read(fd, 33)
        if len(raw) != 32:
            raise UnsafeResearchCustodian("invalid key length")
        return Ed25519PrivateKey.from_private_bytes(raw)
    finally:
        os.close(fd)


def _pub64(value: object) -> Ed25519PublicKey:
    if not isinstance(value, str) or len(value) > 64:
        raise UnsafeResearchCustodian("invalid base64 public key")
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32:
        raise UnsafeResearchCustodian("public key is not Ed25519-32")
    return Ed25519PublicKey.from_public_bytes(raw)


def _execute(root: Path, *, command: str, request: object) -> dict:
    _root_ok(root)
    # Lazy import keeps this low-level signer safe to use by the ledger,
    # without an import cycle. All signing/sequence checks are delegated
    # to this ONE local SQLite journal; no caller-supplied recovery path.
    from v2_custody_signing_ledger import SyntheticV2CustodyLedger
    if command == "bootstrap":
        if request != {}:
            raise UnsafeResearchCustodian("bootstrap takes no attached claims")
        signer = _load_key(root, create=True)
        SyntheticV2CustodyLedger(root, signer).initialize()
        pub = signer.public_key()
        return {
            "schema": 1, "mode": "synthetic_v2_custodian_bootstrap",
            "custodian_public_base64": base64.b64encode(
                pub.public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw)).decode("ascii"),
            "custodian_fingerprint": public_fingerprint(pub),
            "decision": "HOLD", "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0,
        }
    if command not in ("sign", "readback", "revoke") or not isinstance(request, dict):
        raise UnsafeResearchCustodian("unsupported synthetic signer operation")
    signer = _load_key(root, create=False)
    ledger = SyntheticV2CustodyLedger(root, signer)
    if command == "readback":
        if set(request) != {"generation", "witness_head", "observer_head", "scope"}:
            raise UnsafeResearchCustodian("unknown readback fields")
        for name in ("witness_head", "observer_head"):
            part = request[name]
            if (not isinstance(part, list) or len(part) != 2
                    or type(part[0]) is not int or not isinstance(part[1], str)):
                raise UnsafeResearchCustodian("bad readback scope")
        return ledger.readback(
            generation=request["generation"],
            witness_head=tuple(request["witness_head"]),
            observer_head=tuple(request["observer_head"]),
            scope=request["scope"])
    if command == "revoke":
        if set(request) != {"generation", "checkpoint_sha256"}:
            raise UnsafeResearchCustodian("unknown revocation fields")
        return ledger.revoke(
            generation=request["generation"],
            checkpoint_sha256=request["checkpoint_sha256"])
    fields = {
        "witness_receipt", "witness_public_base64",
        "witness_pin", "observer_public_base64",
        "worker_fingerprint", "observer_id", "worker_id",
        "witness_head", "observer_head", "scope", "generation",
        "previous_checkpoint_sha256", "issued_at", "expires_at",
    }
    if set(request) != fields:
        raise UnsafeResearchCustodian("missing/unrecognized signer request claims")
    for item in ("witness_head", "observer_head"):
        value = request[item]
        if (not isinstance(value, list) or len(value) != 2
                or type(value[0]) is not int
                or not isinstance(value[1], str)):
            raise UnsafeResearchCustodian("wrong scoped checkpoint head")
    return ledger.sign_once(request)


def _no_duplicate_fields(items):
    result = {}
    for key, value in items:
        if key in result:
            raise UnsafeResearchCustodian("duplicate request JSON field")
        result[key] = value
    return result


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--command", choices=("bootstrap", "sign", "readback", "revoke"), required=True)
    args = parser.parse_args()
    try:
        line = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if not line or len(line) > MAX_REQUEST:
            raise UnsafeResearchCustodian("missing or oversized research request")
        request = json.loads(
            line, object_pairs_hook=_no_duplicate_fields)
        result = _execute(args.root, command=args.command, request=request)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (UnsafeResearchCustodian, UnsafeCustodyCheckpoint,
            OSError, ValueError, TypeError, KeyError, UnicodeError,
            binascii.Error, sqlite3.Error, json.JSONDecodeError):
        print(json.dumps({
            "decision": "HOLD", "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0,
            "error": "synthetic custody checkpoint denied",
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
