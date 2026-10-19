#!/usr/bin/env python3
"""Restart-durable, deny-only *synthetic* worker acceptance receipt journal.

Stores verified Ed25519 commit/delivery statements in a separate SQLite DB.
Every inspection or mutation requires an independently SUPPLIED exact head
anchor (sequence, SHA-256). An old valid DB + an old supplied anchor will
still verify: permanent independent custody is NOT implemented. The worker
signer key is supplied in RAM and never stored in this DB.

This does not make an independently attested worker, verify a real kernel
transaction, atomically couple the worker lease DB and receipt journal,
authorize any model, or establish durable observer-ACK publication.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from observer_checkpoint import ZERO, _json_bytes, public_fingerprint
from worker_acceptance_receipt import FIELDS, verify as verify_worker_receipt

APP_ID = 0x4F475752  # OGWR, distinct from lease and observer SQLite schemas
SCHEMA_VERSION = 1
MAX_RECORDS = 128
MAX_RECEIPT_BYTES = 2048
MAX_DB_BYTES = 1024 * 1024


class UnsafeWorkerReceipt(ValueError):
    """No automatic repairs, retries, rollbacks or forced un-fencing."""


def _record_hash(sequence: int, previous: str, receipt: dict) -> str:
    return hashlib.sha256(_json_bytes({
        "sequence": sequence,
        "previous_record_sha256": previous,
        "signed_receipt": receipt,
    })).hexdigest()


class SyntheticWorkerReceiptJournal:
    """Cooperative research journal with explicit externally pinned head.

    File hygiene / SQLite transaction checks apply to a cooperating local
    user; a privileged or same-UID adversary may replace DB + trusted head.
    """

    def __init__(self, path: Path, *, worker_public_key: Ed25519PublicKey,
                 worker_id: str, observer_id: str,
                 observer_fingerprint: str,
                 signer: Ed25519PrivateKey | None = None,
                 initial_state: str | None = None,
                 create: bool = False):
        self.path = Path(path)
        self.public = worker_public_key
        self.worker_id = worker_id
        self.observer_id = observer_id
        self.observer_fingerprint = observer_fingerprint
        self.pin = public_fingerprint(worker_public_key)
        self.signer = signer
        self._lock = threading.RLock()
        if self.pin == observer_fingerprint:
            raise UnsafeWorkerReceipt("worker/observer signers must be distinct")
        if signer is not None and public_fingerprint(signer.public_key()) != self.pin:
            raise UnsafeWorkerReceipt("worker signer differs from pinned public key")
        if (not isinstance(worker_id, str) or not isinstance(observer_id, str)
                or worker_id == observer_id
                or not isinstance(observer_fingerprint, str)
                or len(observer_fingerprint) != 64
                or any(c not in "0123456789abcdef" for c in observer_fingerprint)):
            raise UnsafeWorkerReceipt("untrusted role or observer fingerprint")
        self._initialize(create=create, initial_state=initial_state)

    def _valid_path(self, *, missing_ok=False) -> bool:
        if not self.path.is_absolute() or self.path.suffix != ".sqlite3":
            raise UnsafeWorkerReceipt("absolute SQLite DB path required")
        parent = self.path.parent
        if not parent.is_dir() or parent.is_symlink():
            raise UnsafeWorkerReceipt("untrusted parent path")
        owner = parent.stat()
        if owner.st_uid != os.getuid() or owner.st_mode & 0o077:
            raise UnsafeWorkerReceipt("parent must be owner-private")
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return False
            raise UnsafeWorkerReceipt("missing receipt DB; no implicit reset")
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise UnsafeWorkerReceipt("receipt DB must be private regular file")
        return True

    def _connect(self):
        self._valid_path()
        uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
        db = sqlite3.connect(uri, uri=True, timeout=3, isolation_level=None)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA busy_timeout=3000")
            if self.path.stat().st_size > MAX_DB_BYTES:
                raise UnsafeWorkerReceipt("receipt DB size exceeded")
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise UnsafeWorkerReceipt("corrupt receipt DB")
            if (db.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                    or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
                raise UnsafeWorkerReceipt("wrong SQLite application/schema")
            meta = db.execute(
                "SELECT worker_id,observer_id,worker_pin,observer_pin FROM metadata WHERE id=1"
            ).fetchone()
            if meta != (self.worker_id, self.observer_id, self.pin, self.observer_fingerprint):
                raise UnsafeWorkerReceipt("worker key or role mismatch")
            return db
        except BaseException:
            db.close()
            raise

    def _initialize(self, *, create: bool, initial_state: str | None):
        exists = self._valid_path(missing_ok=create)
        if create:
            if exists:
                raise UnsafeWorkerReceipt("existing journal cannot be overwritten")
            if (not isinstance(initial_state, str) or len(initial_state) != 64
                    or any(c not in "0123456789abcdef" for c in initial_state)):
                raise UnsafeWorkerReceipt("initial state digest required")
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
            try:
                db = sqlite3.connect(str(self.path), isolation_level=None)
                try:
                    db.execute("PRAGMA journal_mode=DELETE")
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute(f"PRAGMA application_id={APP_ID}")
                    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("""CREATE TABLE metadata (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        worker_id TEXT NOT NULL,
                        observer_id TEXT NOT NULL,
                        worker_pin TEXT NOT NULL,
                        observer_pin TEXT NOT NULL,
                        initial_state TEXT NOT NULL
                    )""")
                    db.execute("""CREATE TABLE receipts (
                        sequence INTEGER PRIMARY KEY,
                        kind TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        signed_receipt TEXT NOT NULL,
                        previous_head TEXT NOT NULL,
                        head_sha256 TEXT NOT NULL
                    )""")
                    db.execute("INSERT INTO metadata VALUES (1,?,?,?,?,?)",
                               (self.worker_id, self.observer_id, self.pin,
                                self.observer_fingerprint, initial_state))
                    db.execute("COMMIT")
                finally:
                    db.close()
            except BaseException:
                self.path.unlink(missing_ok=True)
                raise
        with self._transaction(write=False):
            pass

    def _validate(self, db):
        meta = db.execute(
            "SELECT initial_state FROM metadata WHERE id=1"
        ).fetchone()
        if meta is None or db.execute(
                "SELECT count(*) FROM metadata").fetchone()[0] != 1:
            raise UnsafeWorkerReceipt("missing receipt baseline")
        state = meta[0]
        if (not isinstance(state, str) or len(state) != 64
                or any(c not in "0123456789abcdef" for c in state)):
            raise UnsafeWorkerReceipt("invalid receipt baseline")
        rows = db.execute(
            "SELECT sequence,kind,operation_id,signed_receipt,"
            "previous_head,head_sha256 FROM receipts ORDER BY sequence"
        ).fetchall()
        if len(rows) > MAX_RECORDS:
            raise UnsafeWorkerReceipt("too many receipt events")
        head = ZERO
        prior = None
        generation = 1
        for n, (seq, kind, op, raw, previous, digest) in enumerate(rows, 1):
            if (seq != n or kind not in FIELDS or not isinstance(raw, str)
                    or len(raw.encode("utf-8")) > MAX_RECEIPT_BYTES
                    or previous != head):
                raise UnsafeWorkerReceipt("skipped, overlong or forked receipt")
            try:
                import json
                envelope = json.loads(raw)
                if raw != _json_bytes(envelope).decode("ascii"):
                    raise UnsafeWorkerReceipt("noncanonical receipt JSON")
            except (ValueError, TypeError, UnicodeError) as exc:
                raise UnsafeWorkerReceipt("invalid stored signed receipt") from exc
            claims = envelope.get("payload") if isinstance(envelope, dict) else None
            if not isinstance(claims, dict) or set(claims) != FIELDS[kind]:
                raise UnsafeWorkerReceipt("wrong signed receipt type")
            expected = {k: claims[k] for k in FIELDS[kind] - {"schema", "kind"}}
            if not verify_worker_receipt(
                    envelope=envelope, public_key=self.public,
                    trusted_fingerprint=self.pin, expected_kind=kind,
                    expected=expected, observer_fingerprint=self.observer_fingerprint):
                raise UnsafeWorkerReceipt("forged or invalid worker signature")
            if (claims["worker_id"] != self.worker_id
                    or claims["observer_id"] != self.observer_id
                    or claims["operation_id"] != op):
                raise UnsafeWorkerReceipt("wrong worker, observer or operation")
            if kind == "synthetic_worker_commit":
                if (prior is not None and prior["kind"] != "synthetic_worker_delivery"):
                    raise UnsafeWorkerReceipt("unresolved prior commit")
                if (claims["before_state_sha256"] != state
                        or claims["generation"] != generation + 1):
                    raise UnsafeWorkerReceipt("non-sequential worker state")
                generation += 1
                state = claims["after_state_sha256"]
            else:
                if prior is None or prior["kind"] != "synthetic_worker_commit":
                    raise UnsafeWorkerReceipt("orphan worker delivery")
                cp = prior
                if any(claims[k] != cp[k] for k in
                       FIELDS["synthetic_worker_commit"] - {"schema", "kind"}):
                    raise UnsafeWorkerReceipt("worker delivery not bound to commit")
            if _record_hash(n, previous, envelope) != digest:
                raise UnsafeWorkerReceipt("tampered or stale chain digest")
            head = digest
            prior = {**claims, "kind": kind}
        return (len(rows), head), state, (
            prior is not None and prior["kind"] == "synthetic_worker_commit"), prior

    @contextmanager
    def _transaction(self, *, write=True):
        with self._lock:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                current = self._validate(db)
                yield db, current
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            finally:
                db.close()

    @staticmethod
    def _require_anchor(actual, anchor):
        if (not isinstance(anchor, tuple) or len(anchor) != 2
                or type(anchor[0]) is not int
                or not isinstance(anchor[1], str)
                or actual != anchor):
            raise UnsafeWorkerReceipt("missing, stale or untrusted external head")

    def inspect(self, *, trusted_anchor: tuple[int, str],
                observed_worker_state_sha256: str | None = None) -> dict:
        with self._transaction(write=False) as (_, (anchor, state, pending, _prior)):
            self._require_anchor(anchor, trusted_anchor)
            matching = (observed_worker_state_sha256 == state)
            return {
                "schema": 1, "mode": "synthetic_signed_worker_journal",
                "record_count": anchor[0],
                "head_sha256": anchor[1],
                "last_worker_state_sha256": state,
                "worker_state_matches_supplied_digest": matching,
                "commit_pending_delivery": pending,
                "independent_worker_key_custody_verified": False,
                "source_authority_verified": False,
                "rollback_resistance_verified": False,
                "agent_dispatch_authorized": False,
                "model_egress_authorized": False,
                "provider_requests_forwarded": 0,
                "decision": "HOLD",
            }

    def append(self, *, kind: str, fields: dict,
               trusted_anchor: tuple[int, str]) -> tuple[tuple[int, str], dict]:
        if self.signer is None:
            raise UnsafeWorkerReceipt("read-only verifier cannot write receipts")
        if kind not in FIELDS:
            raise UnsafeWorkerReceipt("unknown worker receipt kind")
        from worker_acceptance_receipt import sign_fixture
        with self._transaction() as (db, (anchor, state, pending, prior)):
            self._require_anchor(anchor, trusted_anchor)
            if anchor[0] >= MAX_RECORDS:
                raise UnsafeWorkerReceipt("receipt journal capacity reached")
            if kind == "synthetic_worker_commit":
                if pending or (prior is not None and prior["kind"] != "synthetic_worker_delivery"):
                    raise UnsafeWorkerReceipt("pending prior operation")
                if (fields.get("before_state_sha256") != state
                        or type(fields.get("generation")) is not int
                        or fields["generation"] != (
                            prior["generation"] + 1 if prior else 2)):
                    raise UnsafeWorkerReceipt("stale or incorrect committed state")
            else:
                if not pending or prior is None:
                    raise UnsafeWorkerReceipt("delivery without pending commit")
                if any(fields.get(k) != prior[k] for k in
                       FIELDS["synthetic_worker_commit"] - {"schema", "kind"}):
                    raise UnsafeWorkerReceipt("delivery does not match committed operation")
            signed = sign_fixture(signer=self.signer, kind=kind, fields=fields)
            raw = _json_bytes(signed)
            if len(raw) > MAX_RECEIPT_BYTES:
                raise UnsafeWorkerReceipt("signed receipt too large")
            next_seq = anchor[0] + 1
            updated_head = _record_hash(next_seq, anchor[1], signed)
            db.execute(
                "INSERT INTO receipts VALUES (?,?,?,?,?,?)",
                (next_seq, kind, fields["operation_id"], raw.decode("ascii"),
                 anchor[1], updated_head))
            # Completed SQLite transaction before caller receives head.
            return (next_seq, updated_head), signed


if __name__ == "__main__":
    raise SystemExit("Research-only signed receipts, no agent admission")
