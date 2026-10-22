#!/usr/bin/env python3
"""Disposable V2 witness-controlled Ed25519 acceptance-receipt signer.

A separate witness private key is generated and saved only inside a 0700
research fixture root on the witness node; one-shot SSH processes can reopen
it. No production key, independent administrative custodian, immutable
high-water, model or agent authorization is involved. The witness can sign
the same old DB after an attacker restores both the log and anchor.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from capability_gate import _decode, _encode
from observer_checkpoint import _json_bytes, public_fingerprint
from v2_offhost_marker_witness import (
    HEX64, ROLE, V2OffhostMarkerWitness, UnsafeOffhostWitness,
)

DOMAIN = b"herdr-synthetic-v2-witness-countersign-v1\0"
FIELDS = {
    "schema", "kind", "witness_fingerprint", "observer_fingerprint",
    "worker_fingerprint", "observer_id", "worker_id",
    "witness_record_count", "witness_head_sha256",
    "observer_revision", "observer_marker_sha256",
    "observer_stage", "agent_dispatch_authorized",
    "provider_requests_forwarded",
}
STAGE_NAMES = (
    "empty", "publication_fenced", "ack_signed_fenced",
    "delivered", "quarantined_untrusted",
)


class UnsafeCountersign(ValueError):
    """Untrusted, tampered, stale, or improperly pinned research receipt."""


def _private_fixture_key(path: Path, *, create=False) -> Ed25519PrivateKey:
    if not path.is_absolute() or path.name != "witness-ephemeral.key":
        raise UnsafeCountersign("only explicit disposable witness key path")
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise UnsafeCountersign("private witness-key directory missing")
    st = parent.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise UnsafeCountersign("witness key directory not owner-private")
    if create:
        generated = Ed25519PrivateKey.generate()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            raw = generated.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption())
            if os.write(fd, raw) != len(raw):
                raise UnsafeCountersign("short fixture private-key write")
            os.fsync(fd)
        finally:
            os.close(fd)
        parentfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parentfd)
        finally:
            os.close(parentfd)
        return generated
    fd = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0))
    try:
        s = os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1
                or s.st_uid != os.getuid() or s.st_mode & 0o077
                or s.st_size != 32):
            raise UnsafeCountersign("unsafe research signing-key inode")
        raw = os.read(fd, 33)
        if len(raw) != 32:
            raise UnsafeCountersign("invalid ephemeral Ed25519 secret length")
        return Ed25519PrivateKey.from_private_bytes(raw)
    finally:
        os.close(fd)


def _valid_receipt_payload(p: object) -> bool:
    if not isinstance(p, dict) or set(p) != FIELDS:
        return False
    return (
        type(p["schema"]) is int and p["schema"] == 1
        and p["kind"] == "synthetic_v2_witness_receipt"
        and all(isinstance(p[k], str) and HEX64.fullmatch(p[k])
                for k in ("witness_fingerprint", "observer_fingerprint",
                          "worker_fingerprint", "witness_head_sha256",
                          "observer_marker_sha256"))
        and len({p["witness_fingerprint"], p["observer_fingerprint"],
                 p["worker_fingerprint"]}) == 3
        and all(isinstance(p[k], str) and ROLE.fullmatch(p[k])
                for k in ("observer_id", "worker_id"))
        and p["observer_id"] != p["worker_id"]
        and type(p["witness_record_count"]) is int
        and 1 <= p["witness_record_count"] <= 4
        and type(p["observer_revision"]) is int
        and p["observer_revision"] in range(5)
        and p["observer_stage"] == STAGE_NAMES[p["observer_revision"]]
        and p["agent_dispatch_authorized"] is False
        and type(p["provider_requests_forwarded"]) is int
        and p["provider_requests_forwarded"] == 0
    )


def verify_witness_receipt(envelope: object, *,
                           trusted_witness_key: Ed25519PublicKey,
                           expected_witness_pin: str,
                           observer_public_key: Ed25519PublicKey,
                           worker_fingerprint: str,
                           observer_id: str, worker_id: str,
                           expected_witness_head: tuple[int, str],
                           expected_observer_head: tuple[int, str]) -> bool:
    """Check exact source, head and signature; never confer model admission."""
    if (not isinstance(envelope, dict)
            or set(envelope) != {"algorithm", "payload", "signature"}
            or envelope.get("algorithm") != "Ed25519"
            or not _valid_receipt_payload(envelope.get("payload"))
            or not isinstance(expected_witness_head, tuple)
            or not isinstance(expected_observer_head, tuple)
            or len(expected_witness_head) != 2
            or len(expected_observer_head) != 2
            or type(expected_witness_head[0]) is not int
            or type(expected_observer_head[0]) is not int
            or not isinstance(expected_witness_pin, str)
            or public_fingerprint(trusted_witness_key) != expected_witness_pin):
        return False
    p = envelope["payload"]
    if (p["witness_fingerprint"] != expected_witness_pin
            or p["observer_fingerprint"] != public_fingerprint(observer_public_key)
            or p["worker_fingerprint"] != worker_fingerprint
            or p["observer_id"] != observer_id
            or p["worker_id"] != worker_id
            or (p["witness_record_count"], p["witness_head_sha256"])
               != expected_witness_head
            or (p["observer_revision"], p["observer_marker_sha256"])
               != expected_observer_head):
        return False
    try:
        trusted_witness_key.verify(
            _decode(envelope["signature"]), DOMAIN + _json_bytes(p))
        return True
    except (InvalidSignature, ValueError, UnicodeError, TypeError):
        return False


def countersign(witness: V2OffhostMarkerWitness, *,
                signer: Ed25519PrivateKey, witness_head: tuple[int, str],
                observer_head: tuple[int, str]) -> dict:
    state = witness.inspect(
        trusted_anchor=witness_head,
        expected_marker_head=observer_head)
    payload = {
        "schema": 1,
        "kind": "synthetic_v2_witness_receipt",
        "witness_fingerprint": public_fingerprint(signer.public_key()),
        "observer_fingerprint": public_fingerprint(witness.key),
        "worker_fingerprint": witness.worker_pin,
        "observer_id": witness.observer_id,
        "worker_id": witness.worker_id,
        "witness_record_count": state["records"],
        "witness_head_sha256": state["latest_record_sha256"],
        "observer_revision": state["marker_revision"],
        "observer_marker_sha256": state["marker_sha256"],
        "observer_stage": state["marker_stage"],
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
    }
    if not _valid_receipt_payload(payload):
        raise UnsafeCountersign("not a valid signed-witness state")
    return {
        "algorithm": "Ed25519", "payload": payload,
        "signature": _encode(signer.sign(DOMAIN + _json_bytes(payload))),
    }


def _one_shot_cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--command", choices=("bootstrap", "append", "inspect"),
                        required=True)
    parser.add_argument("--log-name", required=True)
    parser.add_argument("--observer-public-base64", required=True)
    parser.add_argument("--observer-id", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--worker-pin", required=True)
    args = parser.parse_args()
    try:
        if (not args.root.is_absolute()
                or args.root.parent != Path.home() / ".cache"
                or re.fullmatch(r"og-v2-countersign-[A-Za-z0-9]{6}",
                                args.root.name) is None
                or args.root.is_symlink()
                or args.log_name not in (
                    "primary", "quarantine", "old_image")):
            raise UnsafeCountersign("unapproved disposable research location")
        raw_public = base64.b64decode(
            args.observer_public_base64, validate=True)
        if len(raw_public) != 32:
            raise UnsafeCountersign("wrong observer-key length")
        obs_key = Ed25519PublicKey.from_public_bytes(raw_public)
        keypath = args.root / "witness-ephemeral.key"
        if args.command == "bootstrap":
            if args.log_name != "primary":
                raise UnsafeCountersign("must bootstrap primary witness role")
            signer = _private_fixture_key(keypath, create=True)
            data = {
                "schema": 1,
                "witness_public_base64": base64.b64encode(
                    signer.public_key().public_bytes(
                        serialization.Encoding.Raw,
                        serialization.PublicFormat.Raw,
                    )).decode("ascii"),
                "witness_fingerprint": public_fingerprint(signer.public_key()),
                "agent_dispatch_authorized": False,
                "provider_requests_forwarded": 0,
                "decision": "HOLD",
            }
        else:
            signer = _private_fixture_key(keypath)
            witness = V2OffhostMarkerWitness(
                args.root / (args.log_name + ".jsonl"),
                observer_public_key=obs_key,
                observer_id=args.observer_id, worker_id=args.worker_id,
                worker_pin=args.worker_pin)
            raw_line = sys.stdin.buffer.readline(12 * 1024 + 1)
            if not raw_line or len(raw_line) > 12 * 1024:
                raise UnsafeCountersign("missing or oversized request")
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise UnsafeCountersign("non-JSON-object request")
            if args.command == "append":
                if set(request) != {"marker", "trusted_anchor"}:
                    raise UnsafeCountersign("wrong append request scope")
                prior = request["trusted_anchor"]
                head = witness.append(
                    request["marker"],
                    trusted_anchor=tuple(prior) if prior is not None else None,
                    genesis=prior is None)
                marker = request["marker"]
                obs_head = (
                    marker["payload"]["revision"],
                    hashlib.sha256(_json_bytes(marker)).hexdigest())
            else:
                if set(request) != {"trusted_anchor", "expected_marker_head"}:
                    raise UnsafeCountersign("wrong inspect request scope")
                head = tuple(request["trusted_anchor"])
                obs_head = tuple(request["expected_marker_head"])
            signed = countersign(
                witness, signer=signer,
                witness_head=head, observer_head=obs_head)
            data = {
                "schema": 1, "witness_head": head, "observer_head": obs_head,
                "signed_receipt": signed, "decision": "HOLD",
                "agent_dispatch_authorized": False,
                "provider_requests_forwarded": 0,
            }
        print(json.dumps(data, sort_keys=True))
        return 0
    except (UnsafeCountersign, UnsafeOffhostWitness, OSError, ValueError,
            TypeError, KeyError, UnicodeError, binascii.Error):
        print(json.dumps({
            "decision": "HOLD", "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0,
            "error": "offhost receipt denied",
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(_one_shot_cli())
