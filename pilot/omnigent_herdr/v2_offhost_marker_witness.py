#!/usr/bin/env python3
"""Bounded cooperative OFF-HOST witness for signed V2 observer stage markers.

Each observer-signed stage marker is verified against a pinned PUBLIC key.
A private, fsync'd, append-only *fixture* log is kept on a distinct host.
Caller must present latest exact (record_count, SHA256-of-last-record)
to append or inspect. A returned marker/head is not third-party custody.

No private signing key, agent, model, provider, network service, daemon,
production persistence, automatic retry, or production admission.
This file can be executed as a one-shot CLI over already trusted SSH.
A shared operator/root may roll back the off-host log and its anchor.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from capability_gate import _decode
from observer_checkpoint import ZERO, _json_bytes, public_fingerprint
from v2_durable_observer_inbox import DOMAIN, MARK_FIELDS, STAGES

MAX_LINE = 3072
MAX_LOG = 24 * 1024
MAX_MARKERS = 4
HEX64 = re.compile(r"^[a-f0-9]{64}$")
OP = re.compile(r"^[a-f0-9]{32}$")
ROLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


class UnsafeOffhostWitness(ValueError):
    """Denied observation, regression, forbidden stage or changed log."""


def _hash(value: dict) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _marker_ok(marker: object, *, key: Ed25519PublicKey,
               observer_id: str, worker_id: str, worker_pin: str) -> bool:
    if (not isinstance(marker, dict)
            or set(marker) != {"algorithm", "payload", "signature"}
            or marker["algorithm"] != "Ed25519"
            or not isinstance(marker["payload"], dict)
            or set(marker["payload"]) != MARK_FIELDS):
        return False
    p = marker["payload"]
    if (type(p.get("schema")) is not int or p["schema"] != 1
            or p.get("kind") != "synthetic_v2_observer_inbox_marker"
            or p.get("observer_id") != observer_id
            or p.get("worker_id") != worker_id
            or p.get("observer_pin") != public_fingerprint(key)
            or p.get("worker_pin") != worker_pin
            or type(p.get("revision")) is not int
            or p["revision"] not in range(5)
            or p.get("stage") != STAGES[p["revision"]]
            or not all(isinstance(p.get(k), str) and HEX64.fullmatch(p[k])
                       for k in ("publication_sha256", "ack_sha256",
                                 "delivery_sha256", "before_state_sha256",
                                 "after_state_sha256", "outbox_record_sha256"))
            or type(p.get("outbox_record_no")) is not int
            or not isinstance(p.get("operation_id"), str)):
        return False
    n = p["revision"]
    if n == 0:
        if (p["operation_id"] != "" or p["outbox_record_no"] != 0
                or p["before_state_sha256"] != p["after_state_sha256"]
                or any(p[k] != ZERO for k in (
                    "publication_sha256", "ack_sha256", "delivery_sha256",
                    "outbox_record_sha256"))):
            return False
    elif n == 4:
        if (not OP.fullmatch(p["operation_id"])
                or p["publication_sha256"] == ZERO
                or p["outbox_record_no"] != 0
                or p["before_state_sha256"] != p["after_state_sha256"]
                or any(p[k] != ZERO for k in (
                    "ack_sha256", "delivery_sha256",
                    "outbox_record_sha256"))):
            return False
    else:
        if (not OP.fullmatch(p["operation_id"])
                or p["outbox_record_no"] != 1
                or p["outbox_record_sha256"] == ZERO
                or p["before_state_sha256"] == p["after_state_sha256"]
                or p["publication_sha256"] == ZERO
                or (n == 1 and (p["ack_sha256"] != ZERO or
                                 p["delivery_sha256"] != ZERO))
                or (n == 2 and (p["ack_sha256"] == ZERO or
                                 p["delivery_sha256"] != ZERO))
                or (n == 3 and (p["ack_sha256"] == ZERO or
                                 p["delivery_sha256"] == ZERO))):
            return False
    try:
        key.verify(_decode(marker["signature"]), DOMAIN + _json_bytes(p))
        return True
    except (InvalidSignature, ValueError, TypeError, UnicodeError):
        return False


def _transition_ok(before: dict, after: dict) -> bool:
    a = before["payload"]
    b = after["payload"]
    transition = (a["revision"], b["revision"])
    if transition not in ((0, 1), (1, 2), (2, 3), (0, 4)):
        return False
    if a["revision"] != 0 and b["operation_id"] != a["operation_id"]:
        return False
    if b["before_state_sha256"] != a["before_state_sha256"]:
        return False
    if transition == (0, 4):
        return b["after_state_sha256"] == a["after_state_sha256"]
    if transition == (0, 1):
        return b["ack_sha256"] == b["delivery_sha256"] == ZERO
    return all(b[name] == a[name] for name in (
        "after_state_sha256", "outbox_record_no", "outbox_record_sha256",
        "publication_sha256")) and (
        b["ack_sha256"] == a["ack_sha256"] if transition == (2, 3)
        else b["delivery_sha256"] == ZERO)


class V2OffhostMarkerWitness:
    """Verify all signatures and transitions before returning a read-only head."""

    def __init__(self, path: Path, *, observer_public_key: Ed25519PublicKey,
                 observer_id: str, worker_id: str, worker_pin: str):
        self.path = Path(path)
        self.key = observer_public_key
        self.observer_id = observer_id
        self.worker_id = worker_id
        self.worker_pin = worker_pin
        if (not all(isinstance(x, str) and ROLE.fullmatch(x)
                    for x in (observer_id, worker_id))
                or worker_id == observer_id
                or not isinstance(worker_pin, str)
                or not HEX64.fullmatch(worker_pin)
                or worker_pin == public_fingerprint(observer_public_key)):
            raise UnsafeOffhostWitness("invalid worker/observer trust-root scope")

    def _fd(self, *, create=False, write=False):
        if not self.path.is_absolute() or self.path.suffix != ".jsonl":
            raise UnsafeOffhostWitness("private absolute JSONL path required")
        parent = self.path.parent
        if parent.is_symlink() or not parent.is_dir():
            raise UnsafeOffhostWitness("missing/aliased witness parent")
        owner = parent.stat()
        if owner.st_uid != os.getuid() or owner.st_mode & 0o077:
            raise UnsafeOffhostWitness("witness parent must be owner-private")
        if create and not write:
            raise UnsafeOffhostWitness("read-only witness cannot create")
        flags = (os.O_RDWR if write else os.O_RDONLY)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise UnsafeOffhostWitness("missing or existing witness log") from exc
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                    or st.st_uid != os.getuid() or st.st_mode & 0o077):
                raise UnsafeOffhostWitness("witness file must be private")
            fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _read(self, fd: int) -> list[dict]:
        st = os.fstat(fd)
        if st.st_size > MAX_LOG:
            raise UnsafeOffhostWitness("oversized physical log")
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, MAX_LOG + 1)
        st1 = os.fstat(fd)
        if (len(raw) != st.st_size or
                (st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                != (st1.st_size, st1.st_mtime_ns, st1.st_ctime_ns)):
            raise UnsafeOffhostWitness("witness changed during read")
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise UnsafeOffhostWitness("incomplete witness record")
        lines = raw.splitlines(keepends=True)
        if len(lines) > MAX_MARKERS:
            raise UnsafeOffhostWitness("too many synthetic stages")
        rows = []
        prior = ZERO
        for n, line in enumerate(lines, 1):
            if len(line) > MAX_LINE:
                raise UnsafeOffhostWitness("oversized marker")
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise UnsafeOffhostWitness("malformed marker JSON") from exc
            if (not isinstance(record, dict)
                    or set(record) != {"schema", "record_no",
                                      "previous_record_sha256", "marker"}
                    or type(record["schema"]) is not int
                    or record["schema"] != 1
                    or type(record["record_no"]) is not int
                    or record["record_no"] != n
                    or record["previous_record_sha256"] != prior
                    or line != _json_bytes(record) + b"\n"
                    or not _marker_ok(
                        record["marker"], key=self.key,
                        observer_id=self.observer_id,
                        worker_id=self.worker_id, worker_pin=self.worker_pin)):
                raise UnsafeOffhostWitness("forged or noncanonical signed stage")
            if rows and not _transition_ok(rows[-1]["marker"], record["marker"]):
                raise UnsafeOffhostWitness("skipped, forked or regressed stage")
            if not rows and record["marker"]["payload"]["revision"] != 0:
                raise UnsafeOffhostWitness("missing signed genesis")
            prior = _hash(record)
            rows.append(record)
        return rows

    @staticmethod
    def _anchor(rows: list[dict]) -> tuple[int, str]:
        if not rows:
            raise UnsafeOffhostWitness("unpublished witness is not authority")
        return len(rows), _hash(rows[-1])

    @classmethod
    def _require(cls, rows: list[dict], anchor: tuple[int, str]):
        if (not isinstance(anchor, tuple) or len(anchor) != 2
                or type(anchor[0]) is not int
                or not isinstance(anchor[1], str)
                or not HEX64.fullmatch(anchor[1])
                or anchor != cls._anchor(rows)):
            raise UnsafeOffhostWitness("missing, stale or rolled-back witness head")

    def append(self, marker: dict, *, trusted_anchor: tuple[int, str] | None,
               genesis=False) -> tuple[int, str]:
        fd = self._fd(write=True, create=genesis)
        try:
            rows = self._read(fd)
            if genesis:
                if rows or trusted_anchor is not None:
                    raise UnsafeOffhostWitness("genesis must be a fresh witness")
            else:
                self._require(rows, trusted_anchor)
            if (not _marker_ok(marker, key=self.key,
                               observer_id=self.observer_id,
                               worker_id=self.worker_id, worker_pin=self.worker_pin)
                    or (not rows and marker["payload"]["revision"] != 0)
                    or (rows and not _transition_ok(rows[-1]["marker"], marker))
                    or len(rows) >= MAX_MARKERS):
                raise UnsafeOffhostWitness("invalid signer/stage transition")
            record = {
                "schema": 1,
                "record_no": len(rows) + 1,
                "previous_record_sha256": _hash(rows[-1]) if rows else ZERO,
                "marker": marker,
            }
            line = _json_bytes(record) + b"\n"
            if len(line) > MAX_LINE or os.fstat(fd).st_size + len(line) > MAX_LOG:
                raise UnsafeOffhostWitness("witness record size exceeded")
            os.lseek(fd, 0, os.SEEK_END)
            if os.write(fd, line) != len(line):
                raise UnsafeOffhostWitness("short write, manual review required")
            os.fsync(fd)
            confirmed = self._read(fd)
            if len(confirmed) != len(rows)+1 or _hash(confirmed[-1]) != _hash(record):
                raise UnsafeOffhostWitness("postcommit signature readback failed")
            if genesis:
                parentfd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parentfd)
                finally:
                    os.close(parentfd)
            return self._anchor(confirmed)
        finally:
            os.close(fd)

    def inspect(self, *, trusted_anchor: tuple[int, str],
                expected_marker_head: tuple[int, str]) -> dict:
        fd = self._fd()
        try:
            rows = self._read(fd)
            self._require(rows, trusted_anchor)
            marker = rows[-1]["marker"]
            actual = (marker["payload"]["revision"], _hash(marker))
            if (not isinstance(expected_marker_head, tuple)
                    or len(expected_marker_head) != 2
                    or type(expected_marker_head[0]) is not int
                    or expected_marker_head != actual):
                raise UnsafeOffhostWitness("observer DB image differs from remote witness")
            return {
                "schema": 1, "mode": "synthetic_offhost_v2_marker_witness",
                "records": len(rows),
                "marker_stage": marker["payload"]["stage"],
                "marker_revision": actual[0],
                "marker_sha256": actual[1],
                "latest_record_sha256": trusted_anchor[1],
                # Running this class does NOT prove which physical machine
                # executes it. The bounded two-node Fleet Commander probe
                # supplies separate, observational host evidence.
                "physical_host_separation_verified": False,
                "independent_administrator_verified": False,
                "externally_protected_anchor_verified": False,
                "source_transaction_attested": False,
                "rollback_resistance_verified": False,
                "agent_dispatch_authorized": False,
                "model_egress_authorized": False,
                "provider_requests_forwarded": 0,
                "decision": "HOLD",
            }
        finally:
            os.close(fd)


def _cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--operation", choices=("genesis", "append", "inspect"),
                        required=True)
    parser.add_argument("--observer-public-base64", required=True)
    parser.add_argument("--observer-id", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--worker-pin", required=True)
    args = parser.parse_args()
    try:
        raw = base64.b64decode(args.observer_public_base64, validate=True)
        if len(raw) != 32:
            raise ValueError("bad observer public key length")
        key = Ed25519PublicKey.from_public_bytes(raw)
        witness = V2OffhostMarkerWitness(
            args.path, observer_public_key=key,
            observer_id=args.observer_id,
            worker_id=args.worker_id, worker_pin=args.worker_pin)
        line = sys.stdin.buffer.readline(12 * 1024 + 1)
        if not line or len(line) > 12 * 1024:
            raise UnsafeOffhostWitness("missing or oversized JSON request")
        request = json.loads(line)
        if not isinstance(request, dict):
            raise UnsafeOffhostWitness("request must be JSON object")
        if args.operation == "inspect":
            if set(request) != {"trusted_anchor", "expected_marker_head"}:
                raise UnsafeOffhostWitness("wrong inspection request")
            response = witness.inspect(
                trusted_anchor=tuple(request["trusted_anchor"]),
                expected_marker_head=tuple(request["expected_marker_head"]))
        else:
            if set(request) != {"marker", "trusted_anchor"}:
                raise UnsafeOffhostWitness("wrong publication request")
            anchor = witness.append(
                request["marker"],
                trusted_anchor=(tuple(request["trusted_anchor"])
                                if request["trusted_anchor"] is not None else None),
                genesis=args.operation == "genesis")
            response = {
                "schema": 1, "anchor": anchor,
                "marker_revision": request["marker"]["payload"]["revision"],
                "agent_dispatch_authorized": False,
                "provider_requests_forwarded": 0,
                "decision": "HOLD",
            }
        print(json.dumps(response, sort_keys=True))
        return 0
    except (UnsafeOffhostWitness, ValueError, TypeError, KeyError, OSError,
            json.JSONDecodeError, binascii.Error):
        print(json.dumps({
            "decision": "HOLD", "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0, "error": "witness denied",
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
