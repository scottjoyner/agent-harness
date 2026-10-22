#!/usr/bin/env python3
"""SQLite restart-durable SYNTHETIC lease receipts (not an agent gateway).

SQLite transactions persist local high-water, revocation, issued lease IDs,
and epoch across a cooperative broker restart. An independent signed
checkpoint or anti-rollback hardware root is NOT present: restoring an old
valid DB *can* roll back decisions. No model forwarding/agent execution.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from capability_gate import (
    HEXDIGEST_RE, LABEL_RE, MAX_RECEIPTS, MAX_TTL_SECONDS,
    SyntheticLeaseGate, _plain_label,
)

APP_ID = 0x4F474C53  # "OGLS" - research-only SQLite schema
SCHEMA_VERSION = 1
ATOMIC_OUTBOX_SCHEMA_VERSION = 2
KEY_DOMAIN = b"herdr-durable-synthetic-key-continuity-v1\0"
DENIED = {"status": "denied", "dispatch_authorized": False,
          "provider_requests_forwarded": 0}


class UnsafeLeaseState(ValueError):
    """Fail-closed corrupt, missing, inaccessible or untrusted local state."""


class DurableSyntheticLeaseGate:
    """Research-only restart durability, with explicitly untrusted DB custody.

    Private, dedicated directory and a long-lived test secret MUST be
    supplied separately by the caller. Key bytes are never stored here.
    The DB is not designed to defend against a malicious same-UID user,
    file rollback, compromised kernel, power-loss of an entire filesystem,
    or independently untrusted host process.
    """

    def __init__(self, path: Path, secret: bytes, *, now=time.time,
                 create: bool = False, _atomic_outbox: bool = False):
        self.path = Path(path)
        self._atomic_outbox = _atomic_outbox
        self._schema_version = (
            ATOMIC_OUTBOX_SCHEMA_VERSION if _atomic_outbox else SCHEMA_VERSION)
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("signing key too short")
        if not callable(now):
            raise TypeError("clock must be callable")
        self._now = now
        self._secret = secret
        self._codec = SyntheticLeaseGate(secret, now=now)
        self._lock = threading.RLock()
        self._key_commitment = hmac.digest(secret, KEY_DOMAIN, "sha256").hex()
        self._initialize(create=create)

    def _secure_path(self, *, allow_missing: bool = False) -> bool:
        """Check best-effort cooperative file hygiene; not race-proof."""
        path = self.path
        if not path.is_absolute():
            raise UnsafeLeaseState("lease state path must be absolute")
        if path.name in (".", "..") or not path.name.endswith(".sqlite3"):
            raise UnsafeLeaseState("lease state path must be a named SQLite file")
        parent = path.parent
        if parent.is_symlink() or not parent.is_dir():
            raise UnsafeLeaseState("lease state directory must be real")
        parent_info = parent.stat()
        if (parent_info.st_uid != os.getuid()
                or (parent_info.st_mode & 0o077) != 0):
            raise UnsafeLeaseState("lease directory must be owner-only")
        try:
            info = path.lstat()
        except FileNotFoundError:
            if allow_missing:
                return False
            raise UnsafeLeaseState("lease DB missing: refusing silent reset")
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or (info.st_mode & 0o077) != 0):
            raise UnsafeLeaseState("lease DB must be a private regular file")
        return True

    def _connect(self):
        self._secure_path()
        try:
            # mode=rw is mandatory: no SQLite implicit DB creation.
            from urllib.parse import quote
            uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
            db = sqlite3.connect(uri, uri=True, timeout=3,
                                 isolation_level=None)
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=3000")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            # Explicitly check before any mutation; this does not detect
            # replacement with an old but otherwise valid DB.
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise UnsafeLeaseState("SQLite integrity check failed")
            app = db.execute("PRAGMA application_id").fetchone()[0]
            ver = db.execute("PRAGMA user_version").fetchone()[0]
            if app != APP_ID or ver != self._schema_version:
                raise UnsafeLeaseState("unexpected DB application or schema version")
            key = db.execute("SELECT key_commitment FROM metadata WHERE id=1").fetchone()
            if not key or not hmac.compare_digest(key[0], self._key_commitment):
                raise UnsafeLeaseState("wrong lease key or missing state")
            return db
        except (sqlite3.Error, UnsafeLeaseState) as exc:
            if 'db' in locals():
                db.close()
            raise UnsafeLeaseState("lease DB failed validation") from exc

    def _initialize(self, *, create: bool) -> None:
        exists = self._secure_path(allow_missing=create)
        if create:
            if exists:
                raise UnsafeLeaseState("refusing to overwrite existing lease DB")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags, 0o600)
            os.close(fd)
            try:
                conn = sqlite3.connect(str(self.path), isolation_level=None)
                try:
                    conn.execute("PRAGMA journal_mode=DELETE")
                    conn.execute("PRAGMA synchronous=FULL")
                    conn.execute(f"PRAGMA application_id={APP_ID}")
                    conn.execute(f"PRAGMA user_version={self._schema_version}")
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("""CREATE TABLE metadata (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        epoch INTEGER NOT NULL CHECK(epoch>=1),
                        last_clock INTEGER,
                        key_commitment TEXT NOT NULL
                    )""")
                    conn.execute("""CREATE TABLE leases (
                        jti TEXT PRIMARY KEY,
                        token_sha256 TEXT NOT NULL UNIQUE,
                        epoch INTEGER NOT NULL,
                        last_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_seq>=0),
                        used INTEGER NOT NULL DEFAULT 0 CHECK(used>=0),
                        max_receipts INTEGER NOT NULL CHECK(max_receipts BETWEEN 1 AND 2),
                        revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1))
                    )""")
                    conn.execute("INSERT INTO metadata VALUES (1,1,NULL,?)",
                                 (self._key_commitment,))
                    if self._atomic_outbox:
                        # Same SQLite database and same BEGIN IMMEDIATE as
                        # the synthetic lease debit; never ATTACH another DB.
                        conn.execute("""CREATE TABLE atomic_outbox (
                            record_no INTEGER PRIMARY KEY,
                            operation_id TEXT NOT NULL UNIQUE,
                            lease_jti TEXT NOT NULL,
                            request_sha256 TEXT NOT NULL,
                            before_state_sha256 TEXT NOT NULL,
                            after_state_sha256 TEXT NOT NULL,
                            epoch INTEGER NOT NULL,
                            sequence INTEGER NOT NULL,
                            receipt_number INTEGER NOT NULL,
                            previous_record_sha256 TEXT NOT NULL,
                            record_sha256 TEXT NOT NULL,
                            hmac_sha256 TEXT NOT NULL
                        )""")
                    conn.execute("COMMIT")
                finally:
                    conn.close()
            except Exception:
                self.path.unlink(missing_ok=True)
                raise
        with self._transaction(write=False):
            pass

    @contextmanager
    def _transaction(self, *, write: bool = True):
        with self._lock:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                # Rows must match exactly: no empty DB or duplicate metadata.
                meta = db.execute(
                    "SELECT epoch,last_clock FROM metadata WHERE id=1"
                ).fetchone()
                count = db.execute("SELECT count(*) FROM metadata").fetchone()[0]
                if meta is None or count != 1 or type(meta[0]) is not int or meta[0] < 1:
                    raise UnsafeLeaseState("invalid metadata authority")
                yield db, meta
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            finally:
                db.close()

    @staticmethod
    def _clock(db, last_clock, now: int) -> bool:
        if type(now) is not int:
            return False
        if last_clock is not None and now < last_clock:
            return False
        db.execute("UPDATE metadata SET last_clock=? WHERE id=1", (now,))
        return True

    def mint(self, *, uid: int, owner: str, session: str,
             worktree_sha256: str, model_pin: str,
             ttl_seconds: int = 20, max_receipts: int = MAX_RECEIPTS) -> str:
        # Mirror upstream synthetic-only scope validation.
        if (type(uid) is not int or uid < 0 or
                any(not _plain_label(x) for x in (owner, session, model_pin)) or
                not isinstance(worktree_sha256, str) or
                not HEXDIGEST_RE.fullmatch(worktree_sha256) or
                type(ttl_seconds) is not int or
                not (1 <= ttl_seconds <= MAX_TTL_SECONDS) or
                type(max_receipts) is not int or
                not (1 <= max_receipts <= MAX_RECEIPTS)):
            raise ValueError("invalid synthetic lease scope")
        with self._transaction() as (db, meta):
            now = int(self._now())
            if not self._clock(db, meta[1], now):
                raise UnsafeLeaseState("clock rollback during mint")
            # Stateless per-transaction codec only creates a token. Its
            # in-memory counters are NOT used for durable decisions.
            codec = SyntheticLeaseGate(
                self._secret, now=lambda: now, epoch=meta[0])
            token = codec.mint(uid=uid, owner=owner, session=session,
                               worktree_sha256=worktree_sha256,
                               model_pin=model_pin,
                               ttl_seconds=ttl_seconds,
                               max_receipts=max_receipts)
            claims = codec._verified_claims(token)
            if claims is None:
                raise UnsafeLeaseState("could not verify freshly minted lease")
            db.execute(
                """INSERT INTO leases
                   (jti,token_sha256,epoch,max_receipts)
                   VALUES (?,?,?,?)""",
                (claims["jti"], hashlib.sha256(token.encode()).hexdigest(),
                 meta[0], max_receipts),
            )
            return token

    @staticmethod
    def _state_digest_db(db, meta) -> str:
        rows = db.execute(
            "SELECT jti,token_sha256,epoch,last_seq,used,max_receipts,revoked "
            "FROM leases ORDER BY jti"
        ).fetchall()
        image = json.dumps(
            {"epoch": meta[0], "last_clock": meta[1], "leases": rows},
            sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(image.encode()).hexdigest()

    def _validate_outbox(self, db, meta) -> None:
        if self._atomic_outbox:
            raise UnsafeLeaseState("atomic outbox validator not configured")

    def _commit_outbox(self, db, *, operation_id, request, claims,
                       before_state_sha256, after_state_sha256,
                       epoch, sequence, receipt_number) -> dict:
        raise UnsafeLeaseState("atomic outbox committer not configured")

    def check(self, request: object, *, peer_uid: int, expected_owner: str,
              expected_session: str, expected_worktree_sha256: str,
              expected_model_pin: str, operation_id: str | None = None) -> dict:
        # Version 2 requires an exact synthetic idempotency key. Legacy
        # version 1 must never accept one as a workaround.
        if self._atomic_outbox:
            if (not isinstance(operation_id, str)
                    or not __import__("re").fullmatch(r"[a-f0-9]{32}", operation_id)):
                return DENIED.copy()
        elif operation_id is not None:
            return DENIED.copy()
        if not isinstance(request, dict) or set(request) != {
            "schema", "op", "token", "seq",
        }:
            return DENIED.copy()
        if (type(request["schema"]) is not int or request["schema"] != 1
                or request["op"] != "synthetic_receipt"
                or type(request["seq"]) is not int or request["seq"] < 1
                or type(peer_uid) is not int):
            return DENIED.copy()
        token = request["token"]
        claims = self._codec._verified_claims(token)
        if not isinstance(claims, dict) or set(claims) != {
            "schema", "kind", "epoch", "jti", "uid", "owner",
            "session", "worktree_sha256", "model_pin",
            "issued_at", "expires_at", "max_receipts",
        }:
            return DENIED.copy()
        try:
            with self._transaction() as (db, meta):
                self._validate_outbox(db, meta)
                now = int(self._now())
                if self._atomic_outbox:
                    # A denied request MUST NOT silently modify the lease
                    # clock without producing an outbox intent.
                    if meta[1] is not None and now < meta[1]:
                        return DENIED.copy()
                    before_state_sha256 = self._state_digest_db(db, meta)
                elif not self._clock(db, meta[1], now):
                    return DENIED.copy()
                if (
                    type(claims["schema"]) is not int or claims["schema"] != 1
                    or claims["kind"] != "synthetic_probe_only"
                    or type(claims["epoch"]) is not int or claims["epoch"] != meta[0]
                    or type(claims["uid"]) is not int or claims["uid"] != peer_uid
                    or claims["owner"] != expected_owner
                    or claims["session"] != expected_session
                    or claims["worktree_sha256"] != expected_worktree_sha256
                    or claims["model_pin"] != expected_model_pin
                    or not isinstance(claims["jti"], str)
                    or not __import__('re').fullmatch(r"[a-f0-9]{32}", claims["jti"])
                    or any(type(claims[k]) is not int for k in (
                        "issued_at", "expires_at", "max_receipts"))
                    or not 1 <= claims["max_receipts"] <= MAX_RECEIPTS
                    or not 1 <= claims["expires_at"] - claims["issued_at"] <= MAX_TTL_SECONDS
                    or now < claims["issued_at"] or now >= claims["expires_at"]
                ):
                    return DENIED.copy()
                previous = db.execute(
                    "SELECT token_sha256,epoch,last_seq,used,max_receipts,revoked "
                    "FROM leases WHERE jti=?",
                    (claims["jti"],),
                ).fetchone()
                if previous is None or (
                    not hmac.compare_digest(
                        previous[0], hashlib.sha256(token.encode()).hexdigest())
                    or previous[1] != meta[0]
                    or previous[4] != claims["max_receipts"]
                    or previous[5] != 0
                    or request["seq"] <= previous[2]
                    or previous[3] >= previous[4]
                ):
                    return DENIED.copy()
                if self._atomic_outbox:
                    if db.execute(
                        "SELECT 1 FROM atomic_outbox WHERE operation_id=?",
                        (operation_id,),
                    ).fetchone():
                        return DENIED.copy()
                    if not self._clock(db, meta[1], now):
                        raise UnsafeLeaseState("unexpected outbox clock regression")
                used = previous[3] + 1
                updated = db.execute(
                    """UPDATE leases SET last_seq=?,used=?
                       WHERE jti=? AND last_seq=? AND used=? AND revoked=0""",
                    (request["seq"], used, claims["jti"],
                     previous[2], previous[3]),
                ).rowcount
                if updated != 1:
                    raise UnsafeLeaseState("non-atomic lease update")
                output = {
                    "status": "synthetic_receipt_only",
                    "dispatch_authorized": False,
                    "provider_requests_forwarded": 0,
                    "receipt_number": used,
                }
                if self._atomic_outbox:
                    new_meta = (meta[0], now)
                    after_state_sha256 = self._state_digest_db(db, new_meta)
                    event = self._commit_outbox(
                        db, operation_id=operation_id, request=request,
                        claims=claims, before_state_sha256=before_state_sha256,
                        after_state_sha256=after_state_sha256,
                        epoch=meta[0], sequence=request["seq"],
                        receipt_number=used)
                    output.update({
                        "atomic_outbox_record": event["record_no"],
                        "atomic_outbox_sha256": event["record_sha256"],
                    })
                return output
        except (sqlite3.Error, UnsafeLeaseState):
            return DENIED.copy()

    def revoke(self, token: str) -> bool:
        claims = self._codec._verified_claims(token)
        if not isinstance(claims, dict) or not isinstance(claims.get("jti"), str):
            return False
        try:
            with self._transaction() as (db, meta):
                now = int(self._now())
                if not self._clock(db, meta[1], now):
                    return False
                return db.execute(
                    "UPDATE leases SET revoked=1 WHERE jti=? AND token_sha256=?",
                    (claims["jti"], hashlib.sha256(token.encode()).hexdigest())
                ).rowcount == 1
        except (sqlite3.Error, UnsafeLeaseState):
            return False

    def rotate_epoch(self) -> int:
        with self._transaction() as (db, meta):
            now = int(self._now())
            if not self._clock(db, meta[1], now):
                raise UnsafeLeaseState("clock rollback during epoch rotation")
            epoch = meta[0] + 1
            db.execute("UPDATE metadata SET epoch=? WHERE id=1", (epoch,))
            # Keep revoked and old high-water rows for local investigation.
            return epoch

    def state_digest(self) -> str:
        """Local checkpoint hash, not independently signed/custodied."""
        with self._transaction(write=False) as (db, meta):
            self._validate_outbox(db, meta)
            return self._state_digest_db(db, meta)


if __name__ == "__main__":
    raise SystemExit("Research-only storage module; not a live broker")
