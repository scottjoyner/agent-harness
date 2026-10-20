#!/usr/bin/env python3
"""Disposable RESOURCE-SIDE synthetic debit and signed receipt on Destroyer.

This is deliberately separate from worker-owned lease/outbox SQLite. Here
the source host owns an actual *synthetic* balance and writes the signed
receipt + balance decrement in ONE local SQLite BEGIN IMMEDIATE transaction.
A separate worker can request a scoped operation, but cannot issue a
source-signed receipt. This does not authorize real provider/agent work,
prove privileged resource custody, or give cross-host atomicity.

Never deploy as a service. No server/listener, no production DB operations.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
from pathlib import Path
from urllib.parse import quote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from capability_gate import _decode, _encode
from observer_checkpoint import ZERO, _json_bytes, public_fingerprint

ROOT_RE = re.compile(r"^og-v2-source-[A-Za-z0-9]{6}$")
OP_RE = re.compile(r"^[a-f0-9]{32}$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
SOURCE_ID = "destroyer-synthetic-resource"
SOURCE_DOMAIN = b"og-v2-disposable-resource-local-commit-receipt-v1\0"
FENCE_DOMAIN = b"og-v2-disposable-resource-stale-attempt-fence-v1\0"
STATE_DOMAIN = b"og-v2-disposable-resource-state-v1\0"
APP_ID = 0x4F475352  # OGSR
APP_VER = 2
MAX_RECORDS = 2
MAX_FENCES = 4
MAX_DB = 256 * 1024
PAYLOAD_KEYS = {
    "schema", "kind", "source_id", "source_pin", "record_no", "operation_id",
    "intent_sha256", "previous_record_sha256", "before_state_sha256",
    "after_state_sha256", "balance_before", "balance_after",
    "agent_dispatch_authorized", "provider_requests_forwarded",
}


class UnsafeSourceState(ValueError):
    """Invalid source record, concurrent replay, or untrusted local DB."""


def _digest(event: object) -> str:
    return hashlib.sha256(_json_bytes(event)).hexdigest()


def _state_digest(balance: int, count: int, head: str) -> str:
    return hashlib.sha256(STATE_DOMAIN + _json_bytes({
        "balance": balance, "count": count, "head": head,
    })).hexdigest()


def _private_root(root: Path):
    if (not root.is_absolute() or root.parent != Path.home() / ".cache"
            or ROOT_RE.fullmatch(root.name) is None
            or not root.is_dir() or root.is_symlink()):
        raise UnsafeSourceState("unapproved disposable source path")
    st = root.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise UnsafeSourceState("source fixture directory not private")


def _safe_file(path: Path, *, max_size: int):
    st = path.lstat()    # missing must NEVER silently recreate
    if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
            or st.st_uid != os.getuid() or st.st_mode & 0o077
            or st.st_size < 1 or st.st_size > max_size):
        raise UnsafeSourceState("source fixture file not private or bounded")


def _new_file(path: Path, data: bytes | None = None):
    fd = os.open(
        path, os.O_WRONLY | os.O_EXCL | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if data and os.write(fd, data) != len(data):
            raise UnsafeSourceState("short source signer write")
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_signer(root: Path) -> Ed25519PrivateKey:
    path = root / "source-ephemeral.key"
    _safe_file(path, max_size=32)
    fd = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size != 32:
            raise UnsafeSourceState("source key replaced during open")
        raw = os.read(fd, 33)
        if len(raw) != 32:
            raise UnsafeSourceState("invalid source private key")
        return Ed25519PrivateKey.from_private_bytes(raw)
    finally:
        os.close(fd)


def _payload_valid(payload: object) -> bool:
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
        return False
    return (
        type(payload["schema"]) is int and payload["schema"] == 1
        and payload["kind"] == "synthetic_resource_committed_debit"
        and payload["source_id"] == SOURCE_ID
        and isinstance(payload["source_pin"], str)
        and SHA_RE.fullmatch(payload["source_pin"]) is not None
        and type(payload["record_no"]) is int
        and 1 <= payload["record_no"] <= MAX_RECORDS
        and isinstance(payload["operation_id"], str)
        and OP_RE.fullmatch(payload["operation_id"]) is not None
        and all(isinstance(payload[k], str) and SHA_RE.fullmatch(payload[k])
                for k in ("intent_sha256", "previous_record_sha256",
                          "before_state_sha256", "after_state_sha256"))
        and type(payload["balance_before"]) is int
        and payload["balance_before"] == MAX_RECORDS - payload["record_no"] + 1
        and type(payload["balance_after"]) is int
        and payload["balance_after"] == payload["balance_before"] - 1
        and payload["agent_dispatch_authorized"] is False
        and type(payload["provider_requests_forwarded"]) is int
        and payload["provider_requests_forwarded"] == 0
    )


def verify_source_receipt(receipt: object, *,
                          source_public_key: Ed25519PublicKey,
                          expected_source_pin: str,
                          expected_operation: str,
                          expected_intent_sha256: str,
                          expected_head: tuple[int, str],
                          expected_before_sha256: str,
                          expected_after_sha256: str) -> bool:
    """Check a source-signed local transaction statement against pins.

    This signature is NOT proof the source host is independently custodied,
    nor that a production resource was modified. The source holds a fake
    2-unit balance. The source DB itself must be inspected separately.
    """
    try:
        if (not isinstance(receipt, dict)
                or set(receipt) != {"algorithm", "payload", "signature"}
                or receipt["algorithm"] != "Ed25519"
                or not _payload_valid(receipt.get("payload"))
                or not isinstance(expected_source_pin, str)
                or not SHA_RE.fullmatch(expected_source_pin)
                or public_fingerprint(source_public_key) != expected_source_pin
                or not isinstance(expected_head, tuple)
                or len(expected_head) != 2
                or type(expected_head[0]) is not int
                or not isinstance(expected_head[1], str)
                or not SHA_RE.fullmatch(expected_head[1])):
            return False
        p = receipt["payload"]
        if (p["source_pin"] != expected_source_pin
                or p["operation_id"] != expected_operation
                or p["intent_sha256"] != expected_intent_sha256
                or p["record_no"] != expected_head[0]
                or _digest(receipt) != expected_head[1]
                or p["before_state_sha256"] != expected_before_sha256
                or p["after_state_sha256"] != expected_after_sha256):
            return False
        source_public_key.verify(
            _decode(receipt["signature"]), SOURCE_DOMAIN + _json_bytes(p))
        return True
    except (ValueError, KeyError, TypeError, UnicodeError, InvalidSignature,
            AttributeError):
        return False


def verify_source_corresponds_to_signed_worker_intent(
        *, worker_publication: dict, worker_public_key: Ed25519PublicKey,
        trusted_worker_pin: str, trusted_observer_pin: str,
        expected_worker_id: str, expected_observer_id: str,
        expected_operation_id: str,
        expected_worker_outbox_head: tuple[int, str],
        expected_worker_before_state: str,
        expected_worker_after_state: str,
        source_receipt: dict, source_public_key: Ed25519PublicKey,
        trusted_source_pin: str, expected_source_head: tuple[int, str],
        expected_source_before_state: str,
        expected_source_after_state: str) -> bool:
    """Cross-check two separately signed SYNTHETIC operations.

    The source and worker independently changed DIFFERENT local SQLite DBs.
    This only matches the exact operation identifier and intent digest.
    It does not prove a single distributed atomic debit or true custody.
    """
    from v2_outbox_publication import observer_verify_publication
    if not observer_verify_publication(
            envelope=worker_publication,
            worker_public_key=worker_public_key,
            trusted_worker_fingerprint=trusted_worker_pin,
            trusted_observer_fingerprint=trusted_observer_pin,
            expected_worker_id=expected_worker_id,
            expected_observer_id=expected_observer_id,
            expected_operation_id=expected_operation_id,
            expected_outbox_head=expected_worker_outbox_head,
            expected_before_state=expected_worker_before_state,
            expected_after_state=expected_worker_after_state):
        return False
    event = worker_publication["payload"]["outbox_event"]
    return verify_source_receipt(
        source_receipt, source_public_key=source_public_key,
        expected_source_pin=trusted_source_pin,
        expected_operation=event["operation_id"],
        expected_intent_sha256=event["request_sha256"],
        expected_head=expected_source_head,
        expected_before_sha256=expected_source_before_state,
        expected_after_sha256=expected_source_after_state)


def verify_stale_source_fence(
        fence: object, *, source_public_key: Ed25519PublicKey,
        expected_source_pin: str, expected_operation: str,
        expected_intent_sha256: str,
        expected_prior_head: tuple[int, str],
        expected_observed_head: tuple[int, str]) -> bool:
    """Validate a SOURCE-signed synthetic denial, never a grant or retry.

    This proves that the current *operator-held source signing key*
    signed a denial of a specific stale operation/intent and predecessor
    head. It is not proof of external immutable fencing or real custody.
    """
    try:
        if (not isinstance(fence, dict)
                or set(fence) != {"algorithm", "payload", "signature"}
                or fence["algorithm"] != "Ed25519"
                or not isinstance(fence["payload"],dict)
                or set(fence["payload"]) != {
                    "schema", "kind", "source_id", "source_pin",
                    "operation_id", "intent_sha256", "prior_record",
                    "prior_head", "observed_record", "observed_head",
                    "retry_authorized", "agent_dispatch_authorized",
                    "provider_requests_forwarded"}
                or not isinstance(expected_source_pin,str)
                or SHA_RE.fullmatch(expected_source_pin) is None
                or public_fingerprint(source_public_key) != expected_source_pin
                or not isinstance(expected_operation,str)
                or OP_RE.fullmatch(expected_operation) is None
                or not isinstance(expected_intent_sha256,str)
                or SHA_RE.fullmatch(expected_intent_sha256) is None
                or not isinstance(expected_prior_head,tuple)
                or len(expected_prior_head)!=2
                or not isinstance(expected_observed_head,tuple)
                or len(expected_observed_head)!=2
                or type(expected_prior_head[0]) is not int
                or type(expected_observed_head[0]) is not int
                or not 0 <= expected_prior_head[0] < expected_observed_head[0] <= MAX_RECORDS
                or not all(isinstance(x[1],str) and SHA_RE.fullmatch(x[1])
                           for x in (expected_prior_head,expected_observed_head))):
            return False
        p=fence["payload"]
        if (type(p["schema"]) is not int or p["schema"]!=1
                or p["kind"]!="synthetic_source_stale_attempt_denied"
                or p["source_id"]!=SOURCE_ID
                or p["source_pin"]!=expected_source_pin
                or p["operation_id"]!=expected_operation
                or p["intent_sha256"]!=expected_intent_sha256
                or type(p["prior_record"]) is not int
                or p["prior_record"]!=expected_prior_head[0]
                or p["prior_head"]!=expected_prior_head[1]
                or type(p["observed_record"]) is not int
                or p["observed_record"]!=expected_observed_head[0]
                or p["observed_head"]!=expected_observed_head[1]
                or p["retry_authorized"] is not False
                or p["agent_dispatch_authorized"] is not False
                or type(p["provider_requests_forwarded"]) is not int
                or p["provider_requests_forwarded"]!=0):
            return False
        source_public_key.verify(
            _decode(fence["signature"]), FENCE_DOMAIN + _json_bytes(p))
        return True
    except (InvalidSignature, ValueError, TypeError, KeyError,
            UnicodeError, AttributeError):
        return False


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UnsafeSourceState("duplicate resource request JSON key")
        result[key] = value
    return result


class SyntheticResourceSource:
    """No actual inference, external provider or persistent fleet service."""

    def __init__(self, root: Path, *, create=False):
        self.root = Path(root)
        _private_root(self.root)
        self.dbpath = self.root / "source-debits.sqlite3"
        self.signer = None
        if create:
            self._bootstrap()
        self.signer = _read_signer(self.root)
        self.pin = public_fingerprint(self.signer.public_key())

    def _bootstrap(self):
        signer = Ed25519PrivateKey.generate()
        raw = signer.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
        _new_file(self.root / "source-ephemeral.key", raw)
        _new_file(self.dbpath)
        try:
            db = sqlite3.connect(str(self.dbpath), isolation_level=None)
            try:
                db.execute("PRAGMA journal_mode=DELETE")
                db.execute("PRAGMA synchronous=FULL")
                db.execute(f"PRAGMA application_id={APP_ID}")
                db.execute(f"PRAGMA user_version={APP_VER}")
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE resource (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    balance INTEGER NOT NULL CHECK(balance BETWEEN 0 AND 2),
                    count INTEGER NOT NULL CHECK(count BETWEEN 0 AND 2),
                    last_sha256 TEXT NOT NULL,
                    source_pin TEXT NOT NULL
                )""")
                db.execute("""CREATE TABLE debits (
                    record_no INTEGER PRIMARY KEY,
                    operation_id TEXT NOT NULL UNIQUE,
                    intent_sha256 TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL
                )""")
                db.execute("""CREATE TABLE denied_intents (
                    operation_id TEXT PRIMARY KEY,
                    intent_sha256 TEXT NOT NULL,
                    signed_fence_json TEXT NOT NULL,
                    fence_sha256 TEXT NOT NULL
                )""")
                db.execute("INSERT INTO resource VALUES (1,2,0,?,?)",
                           (ZERO, public_fingerprint(signer.public_key())))
                db.execute("COMMIT")
            finally:
                db.close()
            fd = os.open(self.root, os.O_DIRECTORY | os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        except BaseException:
            # Never silently "repair" partial bootstrap. If this fails
            # the isolated disposable directory is quarantined/deleted.
            raise

    def _open(self):
        _private_root(self.root)
        _safe_file(self.dbpath, max_size=MAX_DB)
        uri = "file:" + quote(str(self.dbpath), safe="/") + "?mode=rw"
        db = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=4)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA busy_timeout=4000")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            if (db.execute("PRAGMA integrity_check").fetchone() != ("ok",)
                    or db.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                    or db.execute("PRAGMA user_version").fetchone()[0] != APP_VER):
                raise UnsafeSourceState("wrong or corrupt source DB")
            return db
        except BaseException:
            db.close()
            raise

    def _inspect_state(self, db):
        resource = db.execute(
            "SELECT balance,count,last_sha256,source_pin FROM resource WHERE id=1"
        ).fetchone()
        if (resource is None
                or db.execute("SELECT count(*) FROM resource").fetchone()[0] != 1
                or resource[3] != self.pin):
            raise UnsafeSourceState("resource state or signing identity missing")
        balance, count, head, _pin = resource
        rows = db.execute(
            "SELECT record_no,operation_id,intent_sha256,receipt_json,receipt_sha256 "
            "FROM debits ORDER BY record_no").fetchall()
        if (type(balance) is not int or type(count) is not int
                or count < 0 or count > MAX_RECORDS
                or balance != MAX_RECORDS - count or len(rows) != count):
            raise UnsafeSourceState("source balance/debit count mismatch")
        previous = ZERO
        for n, (record_no, op, intent, serialized, sha) in enumerate(rows, 1):
            if (type(record_no) is not int or record_no != n
                    or not isinstance(serialized, str)
                    or len(serialized) > 4096):
                raise UnsafeSourceState("source receipt ordering or type invalid")
            try:
                receipt = json.loads(serialized, object_pairs_hook=_unique_fields)
            except (TypeError, ValueError, UnicodeError) as exc:
                raise UnsafeSourceState("malformed stored source receipt") from exc
            if serialized != _json_bytes(receipt).decode("ascii"):
                raise UnsafeSourceState("noncanonical stored source receipt")
            p = receipt.get("payload") if isinstance(receipt, dict) else None
            before = _state_digest(MAX_RECORDS - (n-1), n-1, previous)
            # after-state digest CANNOT include its own receipt digest
            # without circularity. It commits (new balance,count, previous)
            # instead; the receipt SHA becomes the NEXT before-state head.
            after = _state_digest(MAX_RECORDS - n, n, previous)
            if (not isinstance(p, dict)
                    or p.get("previous_record_sha256") != previous
                    or not verify_source_receipt(
                        receipt, source_public_key=self.signer.public_key(),
                        expected_source_pin=self.pin, expected_operation=op,
                        expected_intent_sha256=intent,
                        expected_head=(n, sha),
                        expected_before_sha256=before,
                        expected_after_sha256=after)):
                raise UnsafeSourceState("stored source receipt not signed or scoped")
            previous = sha
        if head != previous:
            raise UnsafeSourceState("source head is not last committed receipt")
        # A stale losing worker must not escape its original declined
        # attempt by resubmitting the SAME operation with a newly
        # observed source head. Denials are independently signed by
        # this disposable SOURCE key, not by the worker/caller.
        fences = db.execute(
            "SELECT operation_id,intent_sha256,signed_fence_json,fence_sha256 "
            "FROM denied_intents ORDER BY operation_id").fetchall()
        if len(fences) > MAX_FENCES:
            raise UnsafeSourceState("too many stale attempt fences")
        committed = {record[1] for record in rows}
        all_heads = [ZERO] + [record[4] for record in rows]
        for op, intent, serialized, sha in fences:
            if (not isinstance(serialized, str) or len(serialized) > 4096
                    or not isinstance(op, str) or OP_RE.fullmatch(op) is None
                    or op in committed or not isinstance(intent, str)
                    or SHA_RE.fullmatch(intent) is None):
                raise UnsafeSourceState("unsafe source denial record")
            try:
                signed = json.loads(
                    serialized, object_pairs_hook=_unique_fields)
                if serialized != _json_bytes(signed).decode("ascii"):
                    raise UnsafeSourceState("noncanonical source fence")
                if (not isinstance(signed, dict)
                        or set(signed) != {"algorithm","payload","signature"}
                        or signed["algorithm"] != "Ed25519"
                        or _digest(signed) != sha):
                    raise UnsafeSourceState("source fence digest mismatch")
                payload = signed["payload"]
                if (not isinstance(payload, dict)
                        or set(payload) != {
                            "schema","kind","source_id","source_pin",
                            "operation_id","intent_sha256",
                            "prior_record","prior_head",
                            "observed_record","observed_head",
                            "retry_authorized","agent_dispatch_authorized",
                            "provider_requests_forwarded"}
                        or type(payload["schema"]) is not int
                        or payload["schema"] != 1
                        or payload["kind"] != "synthetic_source_stale_attempt_denied"
                        or payload["source_id"] != SOURCE_ID
                        or payload["source_pin"] != self.pin
                        or payload["operation_id"] != op
                        or payload["intent_sha256"] != intent
                        or type(payload["prior_record"]) is not int
                        or type(payload["observed_record"]) is not int
                        or not 0 <= payload["prior_record"] < payload["observed_record"] <= count
                        or payload["prior_head"] != all_heads[payload["prior_record"]]
                        or payload["observed_head"] != all_heads[payload["observed_record"]]
                        or payload["retry_authorized"] is not False
                        or payload["agent_dispatch_authorized"] is not False
                        or type(payload["provider_requests_forwarded"]) is not int
                        or payload["provider_requests_forwarded"] != 0):
                    raise UnsafeSourceState("unsafe signed stale attempt fence")
                self.signer.public_key().verify(
                    _decode(signed["signature"]),
                    FENCE_DOMAIN + _json_bytes(payload))
            except (InvalidSignature, ValueError, KeyError,
                    AttributeError, TypeError, UnicodeError) as exc:
                raise UnsafeSourceState("source stale attempt fence untrusted") from exc
        return count, head, balance, rows

    def _persist_stale_fence(self, db, *, operation_id: str,
                             intent_sha256: str,
                             prior: tuple[int,str],
                             observed: tuple[int,str]):
        # Called strictly inside the existing source BEGIN IMMEDIATE
        # transaction after full signature/ledger validation.
        if db.execute("SELECT count(*) FROM denied_intents").fetchone()[0] >= MAX_FENCES:
            raise UnsafeSourceState("stale attempt fence capacity exhausted")
        payload = {
            "schema": 1,
            "kind": "synthetic_source_stale_attempt_denied",
            "source_id": SOURCE_ID,
            "source_pin": self.pin,
            "operation_id": operation_id,
            "intent_sha256": intent_sha256,
            "prior_record": prior[0], "prior_head": prior[1],
            "observed_record": observed[0], "observed_head": observed[1],
            "retry_authorized": False,
            "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0,
        }
        signed = {
            "algorithm": "Ed25519", "payload": payload,
            "signature": _encode(self.signer.sign(
                FENCE_DOMAIN + _json_bytes(payload))),
        }
        canonical = _json_bytes(signed).decode("ascii")
        db.execute("INSERT INTO denied_intents VALUES (?,?,?,?)",
                   (operation_id, intent_sha256, canonical, _digest(signed)))

    def commit(self, *, operation_id: str, intent_sha256: str,
               trusted_head: tuple[int, str]) -> dict:
        if (not isinstance(operation_id, str) or OP_RE.fullmatch(operation_id) is None
                or not isinstance(intent_sha256, str)
                or SHA_RE.fullmatch(intent_sha256) is None
                or type(trusted_head) is not tuple or len(trusted_head) != 2
                or type(trusted_head[0]) is not int
                or not isinstance(trusted_head[1], str)
                or SHA_RE.fullmatch(trusted_head[1]) is None):
            raise UnsafeSourceState("wrong scoped source intent")
        db = self._open()
        try:
            db.execute("BEGIN IMMEDIATE")
            count, head, balance, rows = self._inspect_state(db)
            # Committed or already denied operations are permanently
            # unavailable in this local source image. A newer caller-
            # supplied head cannot "rebase" a rejected operation.
            if db.execute(
                    "SELECT 1 FROM debits WHERE operation_id=?",
                    (operation_id,)).fetchone():
                raise UnsafeSourceState("already committed source operation")
            if db.execute(
                    "SELECT 1 FROM denied_intents WHERE operation_id=?",
                    (operation_id,)).fetchone():
                raise UnsafeSourceState("synthetic stale operation permanently fenced")
            if trusted_head != (count, head):
                # Record an irreversible-by-local-policy DENIAL only for
                # an actually valid old prefix that lost a source race.
                # Unknown/future/forged heads never create a trust record.
                prior_n, prior_sha = trusted_head
                known = [ZERO] + [row[4] for row in rows]
                if (0 <= prior_n < count
                        and prior_sha == known[prior_n]):
                    self._persist_stale_fence(
                        db, operation_id=operation_id,
                        intent_sha256=intent_sha256,
                        prior=trusted_head, observed=(count,head))
                    self._inspect_state(db)
                    db.execute("COMMIT")
                    raise UnsafeSourceState(
                        "stale source head: losing operation fenced")
                raise UnsafeSourceState("untrusted/stale source head")
            if balance <= 0 or count >= MAX_RECORDS:
                raise UnsafeSourceState("source synthetic balance exhausted")
            before = _state_digest(balance, count, head)
            after = _state_digest(balance - 1, count + 1, head)
            payload = {
                "schema": 1, "kind": "synthetic_resource_committed_debit",
                "source_id": SOURCE_ID, "source_pin": self.pin,
                "record_no": count + 1, "operation_id": operation_id,
                "intent_sha256": intent_sha256,
                "previous_record_sha256": head,
                "before_state_sha256": before,
                "after_state_sha256": after,
                "balance_before": balance, "balance_after": balance - 1,
                "agent_dispatch_authorized": False,
                "provider_requests_forwarded": 0,
            }
            if not _payload_valid(payload):
                raise UnsafeSourceState("source record shape not permitted")
            receipt = {
                "algorithm": "Ed25519", "payload": payload,
                "signature": _encode(self.signer.sign(
                    SOURCE_DOMAIN + _json_bytes(payload))),
            }
            sha = _digest(receipt)
            db.execute(
                "INSERT INTO debits VALUES (?,?,?,?,?)",
                (count+1, operation_id, intent_sha256,
                 _json_bytes(receipt).decode("ascii"), sha))
            db.execute(
                "UPDATE resource SET balance=?,count=?,last_sha256=? WHERE id=1",
                (balance-1, count+1, sha))
            # Full replay check happens before committing, and independent
            # readers will verify both DB tables on readback.
            self._inspect_state(db)
            db.execute("COMMIT")
            return self._response(
                count+1, sha, balance-1, receipt, before, after,
                mode="synthetic_resource_commit")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _response(count, sha, balance, receipt, before, after, *, mode):
        return {
            "schema": 1, "mode": mode,
            "head": [count, sha], "balance": balance,
            "receipt": receipt,
            "before_state_sha256": before,
            "after_state_sha256": after,
            "local_debit_and_signed_receipt_atomic": True,
            "independently_administered_source_verified": False,
            "source_is_real_provider": False,
            "agent_dispatch_authorized": False,
            "model_egress_authorized": False,
            "provider_requests_forwarded": 0,
            "decision": "HOLD",
        }

    def recover_committed_operation(
            self, *, operation_id: str, intent_sha256: str,
            trusted_prior_head: tuple[int, str]) -> dict:
        """Read-only recovery of a lost source COMMIT reply.

        A returned receipt is a verified *source-local* effect, never a
        cross-host transaction completion or permission to retry. Caller
        must retain a concrete previously trusted source head; the source
        must still be at precisely the next record after that head.
        Missing/ambiguous/outdated records are DENIED (caller remains
        UNCERTAIN) instead of being interpreted as safe nonexecution.
        """
        if (not isinstance(operation_id, str)
                or OP_RE.fullmatch(operation_id) is None
                or not isinstance(intent_sha256, str)
                or SHA_RE.fullmatch(intent_sha256) is None
                or not isinstance(trusted_prior_head, tuple)
                or len(trusted_prior_head) != 2
                or type(trusted_prior_head[0]) is not int
                or not isinstance(trusted_prior_head[1], str)
                or SHA_RE.fullmatch(trusted_prior_head[1]) is None):
            raise UnsafeSourceState("invalid read-only recovery scope")
        db = self._open()
        try:
            db.execute("BEGIN")
            count, sha, balance, rows = self._inspect_state(db)
            if (count < 1 or trusted_prior_head[0] != count - 1
                    or count > MAX_RECORDS):
                raise UnsafeSourceState("missing or non-immediate source effect")
            # The source's last receipt MUST be the one after the exact
            # supplied prior head; do not 'find' an older record under
            # a newly advanced head.
            last = json.loads(rows[-1][3], object_pairs_hook=_unique_fields)
            payload = last["payload"]
            if (payload["previous_record_sha256"] != trusted_prior_head[1]
                    or payload["operation_id"] != operation_id
                    or payload["intent_sha256"] != intent_sha256):
                raise UnsafeSourceState("not the exact scoped source effect")
            before = payload["before_state_sha256"]
            after = payload["after_state_sha256"]
            db.execute("COMMIT")
            return self._response(count, sha, balance, last, before, after,
                                  mode="synthetic_source_read_only_recovery")
        finally:
            db.close()

    def inspect_stale_denial(
            self, *, operation_id: str, intent_sha256: str,
            trusted_prior_head: tuple[int,str],
            trusted_current_head: tuple[int,str]) -> dict:
        """Read-only verification of a source-local stale-attempt fence.

        An absent fence is NO assurance that a resource debit is safe
        to retry. This never modifies DB or calls provider/agent code.
        """
        if (not isinstance(operation_id,str)
                or OP_RE.fullmatch(operation_id) is None
                or not isinstance(intent_sha256,str)
                or SHA_RE.fullmatch(intent_sha256) is None):
            raise UnsafeSourceState("invalid fenced operation binding")
        db=self._open()
        try:
            db.execute("BEGIN")
            count,head,balance,rows=self._inspect_state(db)
            if (type(trusted_current_head) is not tuple
                    or trusted_current_head != (count,head)):
                raise UnsafeSourceState("missing protected source high-water")
            record=db.execute(
                "SELECT signed_fence_json FROM denied_intents "
                "WHERE operation_id=? AND intent_sha256=?",
                (operation_id,intent_sha256)).fetchone()
            if not record:
                raise UnsafeSourceState("no source signed stale-operation denial")
            envelope=json.loads(record[0],object_pairs_hook=_unique_fields)
            if not verify_stale_source_fence(
                    envelope, source_public_key=self.signer.public_key(),
                    expected_source_pin=self.pin,
                    expected_operation=operation_id,
                    expected_intent_sha256=intent_sha256,
                    expected_prior_head=trusted_prior_head,
                    expected_observed_head=(
                        envelope["payload"]["observed_record"],
                        envelope["payload"]["observed_head"])):
                raise UnsafeSourceState("untrusted source attempt denial")
            db.execute("COMMIT")
            return {
                "schema":1,"mode":"synthetic_stale_source_denial_read_only",
                "head":[count,head],"balance":balance,
                "signed_stale_attempt_fence":envelope,
                "automatic_retry_authorized":False,
                "agent_dispatch_authorized":False,
                "model_egress_authorized":False,
                "provider_requests_forwarded":0,
                "decision":"HOLD",
            }
        finally:
            db.close()

    def inspect(self, *, trusted_head: tuple[int, str]) -> dict:
        db = self._open()
        try:
            db.execute("BEGIN")
            count, sha, balance, rows = self._inspect_state(db)
            if (not isinstance(trusted_head, tuple)
                    or len(trusted_head) != 2 or type(trusted_head[0]) is not int
                    or trusted_head != (count, sha)):
                raise UnsafeSourceState("source head missing or stale")
            receipt = None
            before = after = ZERO
            if rows:
                receipt = json.loads(rows[-1][3])
                before = receipt["payload"]["before_state_sha256"]
                after = receipt["payload"]["after_state_sha256"]
            db.execute("COMMIT")
            return self._response(
                count, sha, balance, receipt, before, after,
                mode="synthetic_resource_read_only")
        finally:
            db.close()


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--command", choices=("bootstrap", "commit", "inspect", "recover", "fence"),
        required=True)
    parser.add_argument("--research-fault",
                        choices=("none", "crash-before-source-commit",
                                 "crash-after-source-commit"),
                        default="none",
                        help="Disposable research CLI process only; NEVER a production flag")
    args = parser.parse_args()
    try:
        line = sys.stdin.buffer.readline(8193)
        if not line or len(line) > 8192:
            raise UnsafeSourceState("missing/oversized source operation")
        request = json.loads(line, object_pairs_hook=_unique_fields)
        if not isinstance(request, dict):
            raise UnsafeSourceState("expected JSON object")
        if (args.research_fault != "none" and args.command != "commit"):
            raise UnsafeSourceState("fault injection is only for fixture commit")
        if args.command == "bootstrap":
            if request != {}:
                raise UnsafeSourceState("bootstrap cannot accept an operation")
            source = SyntheticResourceSource(args.root, create=True)
            result = source.inspect(trusted_head=(0, ZERO))
            result["source_public_base64"] = base64.b64encode(
                source.signer.public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw)).decode("ascii")
            result["source_fingerprint"] = source.pin
        else:
            source = SyntheticResourceSource(args.root)
            if args.command == "commit":
                if set(request) != {"operation_id", "intent_sha256", "trusted_head"}:
                    raise UnsafeSourceState("invalid source commit request")
                if args.research_fault == "crash-before-source-commit":
                    # This exits ONLY the bounded disposable Python worker.
                    # It does not change network or other machine processes.
                    os._exit(96)
                result = source.commit(
                    operation_id=request["operation_id"],
                    intent_sha256=request["intent_sha256"],
                    trusted_head=tuple(request["trusted_head"]))
                if args.research_fault == "crash-after-source-commit":
                    # DB has committed, but the caller receives NO reply.
                    os._exit(97)
            elif args.command == "recover":
                if set(request) != {
                        "operation_id", "intent_sha256", "trusted_prior_head"}:
                    raise UnsafeSourceState("invalid source recovery scope")
                result = source.recover_committed_operation(
                    operation_id=request["operation_id"],
                    intent_sha256=request["intent_sha256"],
                    trusted_prior_head=tuple(request["trusted_prior_head"]))
            elif args.command == "fence":
                if set(request) != {"operation_id","intent_sha256",
                                   "trusted_prior_head","trusted_current_head"}:
                    raise UnsafeSourceState("invalid read-only stale denial scope")
                result = source.inspect_stale_denial(
                    operation_id=request["operation_id"],
                    intent_sha256=request["intent_sha256"],
                    trusted_prior_head=tuple(request["trusted_prior_head"]),
                    trusted_current_head=tuple(request["trusted_current_head"]))
            else:
                if set(request) != {"trusted_head"}:
                    raise UnsafeSourceState("read-only inspect request only")
                result = source.inspect(trusted_head=tuple(request["trusted_head"]))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (UnsafeSourceState, OSError, ValueError, TypeError,
            KeyError, AttributeError, sqlite3.Error, UnicodeError,
            binascii.Error):
        print(json.dumps({
            "decision": "HOLD", "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0, "error": "resource source denied",
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
