#!/usr/bin/env python3
"""Restart-durable, *research-only* synthetic observer ACK journal.

A private SQLite file records signed prepare / checkpoint / ACK transitions.
It fences uncertain delivery across cooperative observer restarts. No
model forwarding, dispatch or independent administrator custody exists.
A restored complete old DB still defeats local high-water. Neither a
valid ACK nor a local 'confirmed' state is production authorization.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import threading
from contextlib import contextmanager
from cryptography.exceptions import InvalidSignature
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from observer_ack_protocol import (
    _signed, verify_ack, verify_prepare,
)
from capability_gate import _decode
from observer_checkpoint import (
    ZERO, CHECKPOINT_DOMAIN, REQUIRED as CHECKPOINT_FIELDS,
    _json_bytes, checkpoint_sha256, public_fingerprint, sign_fixture,
)

APP_ID = 0x4F474A4E  # OGJN, distinct from worker lease SQLite
VERSION = 1
HEX = re.compile(r"^[a-f0-9]{64}$")
OP = re.compile(r"^[a-f0-9]{32}$")
NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$")
STATES = {"prepared", "signed_unconfirmed", "confirmed", "aborted"}


class ObserverJournalError(ValueError):
    """Unsafe, changed, missing, or inconsistent observer journal."""


class ResearchObserverJournal:
    """One observer with restart-durable local state and explicit fencing.

    Caller supplies the SAME ephemeral Ed25519 signer after a cooperative
    restart; it is not stored in the DB. Signing key loss is a HOLD, not a
    reason to regenerate it silently. Durable DB tamper/rollback requires an
    independently administered high-water, which is not implemented here.
    """

    def __init__(self, path: Path, *, signer: Ed25519PrivateKey,
                 observer_id: str, worker_id: str,
                 initial_state: str | None = None, create: bool = False):
        self.path = Path(path)
        self.signer = signer
        self.public_key = signer.public_key()
        self.fingerprint = public_fingerprint(self.public_key)
        self.observer_id = observer_id
        self.worker_id = worker_id
        self._lock = threading.RLock()
        if (not isinstance(observer_id, str) or not NAME.fullmatch(observer_id)
                or not isinstance(worker_id, str) or not NAME.fullmatch(worker_id)
                or observer_id == worker_id):
            raise ObserverJournalError("invalid signer/worker fixture identity")
        self._initialize(create=create, initial_state=initial_state)

    def _path_valid(self, *, missing_ok=False) -> bool:
        if not self.path.is_absolute() or self.path.suffix != ".sqlite3":
            raise ObserverJournalError("journal path must be absolute SQLite path")
        root = self.path.parent
        if root.is_symlink() or not root.is_dir():
            raise ObserverJournalError("journal directory missing or aliased")
        owner = root.stat()
        if owner.st_uid != os.getuid() or owner.st_mode & 0o077:
            raise ObserverJournalError("journal directory must be owner-private")
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return False
            raise ObserverJournalError("journal missing; refuse silent reset")
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ObserverJournalError("journal must be private regular file")
        return True

    def _connect(self):
        self._path_valid()
        uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
        db = sqlite3.connect(uri, uri=True, timeout=3, isolation_level=None)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=3000")
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ObserverJournalError("journal corrupted")
            if (db.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                    or db.execute("PRAGMA user_version").fetchone()[0] != VERSION):
                raise ObserverJournalError("journal schema invalid")
            ident = db.execute(
                "SELECT observer_id,worker_id,public_fingerprint FROM metadata "
                "WHERE id=1"
            ).fetchone()
            if (ident is None or ident != (
                    self.observer_id, self.worker_id, self.fingerprint)):
                raise ObserverJournalError("journal key/role mismatch")
            return db
        except Exception:
            db.close()
            raise

    def _initialize(self, *, create: bool, initial_state: str | None):
        exists = self._path_valid(missing_ok=create)
        if create:
            if exists:
                raise ObserverJournalError("existing journal must not be reset")
            if (not isinstance(initial_state, str)
                    or not HEX.fullmatch(initial_state)):
                raise ObserverJournalError("initial trusted fixture state required")
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags, 0o600)
            os.close(fd)
            try:
                db = sqlite3.connect(str(self.path), isolation_level=None)
                try:
                    db.execute("PRAGMA journal_mode=DELETE")
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute(f"PRAGMA application_id={APP_ID}")
                    db.execute(f"PRAGMA user_version={VERSION}")
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("""CREATE TABLE metadata (
                      id INTEGER PRIMARY KEY CHECK(id=1),
                      observer_id TEXT NOT NULL,
                      worker_id TEXT NOT NULL,
                      public_fingerprint TEXT NOT NULL,
                      generation INTEGER NOT NULL CHECK(generation>=1),
                      checkpoint TEXT NOT NULL
                    )""")
                    db.execute("""CREATE TABLE events (
                      operation_id TEXT PRIMARY KEY,
                      request_sha256 TEXT NOT NULL,
                      before_state TEXT NOT NULL,
                      after_state TEXT,
                      generation INTEGER NOT NULL,
                      prepare TEXT NOT NULL,
                      checkpoint TEXT,
                      ack TEXT,
                      status TEXT NOT NULL CHECK(status IN
                        ('prepared','signed_unconfirmed','confirmed','aborted'))
                    )""")
                    genesis = sign_fixture(
                        observer_key=self.signer, observer_id=self.observer_id,
                        worker_id=self.worker_id, state_sha256=initial_state,
                        generation=1, previous_checkpoint_sha256=ZERO,
                        created_unix=1000)
                    db.execute(
                        "INSERT INTO metadata VALUES (1,?,?,?,?,?)",
                        (self.observer_id, self.worker_id, self.fingerprint,
                         1, json.dumps(genesis, sort_keys=True)))
                    db.execute("COMMIT")
                finally:
                    db.close()
            except Exception:
                self.path.unlink(missing_ok=True)
                raise
        with self._transaction(write=False):
            pass

    def _verify_checkpoint(self, record: object) -> bool:
        if (not isinstance(record, dict)
                or set(record) != {"algorithm", "payload", "signature"}
                or record["algorithm"] != "Ed25519"
                or not isinstance(record["payload"], dict)
                or set(record["payload"]) != CHECKPOINT_FIELDS):
            return False
        p = record["payload"]
        if (p.get("observer_id") != self.observer_id
                or p.get("worker_id") != self.worker_id
                or type(p.get("generation")) is not int
                or not isinstance(p.get("state_sha256"), str)
                or not HEX.fullmatch(p["state_sha256"])):
            return False
        try:
            self.public_key.verify(
                _decode(record["signature"]),
                CHECKPOINT_DOMAIN + _json_bytes(p))
            return True
        except (ValueError, TypeError, UnicodeError, InvalidSignature):
            return False

    def _validate_db(self, db):
        meta = db.execute(
            "SELECT generation,checkpoint FROM metadata WHERE id=1"
        ).fetchone()
        if meta is None or db.execute(
                "SELECT count(*) FROM metadata").fetchone()[0] != 1:
            raise ObserverJournalError("missing observer high-water")
        generation, cp_str = meta
        try:
            cp = json.loads(cp_str)
        except ValueError as exc:
            raise ObserverJournalError("invalid checkpoint storage") from exc
        if (type(generation) is not int or generation < 1
                or not self._verify_checkpoint(cp)
                or cp["payload"]["generation"] != generation):
            raise ObserverJournalError("bad signed observer high-water")
        rows = db.execute(
            "SELECT operation_id,request_sha256,before_state,after_state,"
            "generation,prepare,checkpoint,ack,status FROM events "
            "ORDER BY generation, operation_id"
        ).fetchall()
        pending = []
        committed = []
        for opid, reqsha, before, after, gen, preparation, checkpoint, ack, status in rows:
            if (not OP.fullmatch(opid) or not HEX.fullmatch(reqsha)
                    or not HEX.fullmatch(before) or status not in STATES
                    or type(gen) is not int or gen < 2):
                raise ObserverJournalError("invalid journal event fields")
            try:
                prep = json.loads(preparation)
            except ValueError as exc:
                raise ObserverJournalError("invalid stored prepare") from exc
            # A signed prepare is retained even when cancelled.
            if not verify_prepare(
                    prep, public_key=self.public_key,
                    fingerprint=self.fingerprint,
                    expected={
                        "observer_id": self.observer_id,
                        "worker_id": self.worker_id,
                        "operation_id": opid, "request_sha256": reqsha,
                        "before_state_sha256": before, "generation": gen,
                    }):
                raise ObserverJournalError("invalid signed prepare")
            if status in ("prepared", "signed_unconfirmed"):
                pending.append(opid)
            if status in ("signed_unconfirmed", "confirmed"):
                committed.append((gen, before, after, prep, checkpoint, ack))
                try:
                    signed_cp = json.loads(checkpoint)
                    signed_ack = json.loads(ack)
                except (TypeError, ValueError) as exc:
                    raise ObserverJournalError("missing commit witness") from exc
                if (not self._verify_checkpoint(signed_cp)
                        or signed_cp["payload"]["generation"] != gen
                        or signed_cp["payload"]["state_sha256"] != after
                        or prep["payload"]["previous_checkpoint_sha256"]
                        != signed_cp["payload"]["previous_checkpoint_sha256"]
                        or not verify_ack(
                            signed_ack, public_key=self.public_key,
                            fingerprint=self.fingerprint, expected={
                                "operation_id": opid,
                                "request_sha256": reqsha,
                                "before_state_sha256": before,
                                "after_state_sha256": after,
                                "generation": gen,
                                "previous_checkpoint_sha256":
                                    signed_cp["payload"]["previous_checkpoint_sha256"],
                                "checkpoint_sha256": checkpoint_sha256(signed_cp),
                            })):
                    raise ObserverJournalError("invalid signed commit witness")
            elif checkpoint is not None or ack is not None:
                raise ObserverJournalError("invalid aborted/prepared witness")
        if len(pending) > 1:
            raise ObserverJournalError("multiple in-flight operations")
        # Every signed generation must be represented exactly once. A
        # deleted intermediate receipt cannot silently disappear while
        # signed high-water still claims a later generation.
        committed.sort(key=lambda entry: entry[0])
        if [event[0] for event in committed] != list(range(2, generation + 1)):
            raise ObserverJournalError("missing, duplicate, or future signed generation")
        previous_cp = None
        for event_gen, event_before, event_after, event_prepare, cp_json, _ in committed:
            signed_cp = json.loads(cp_json)
            if previous_cp is not None:
                if (event_before != previous_cp["payload"]["state_sha256"]
                        or signed_cp["payload"]["previous_checkpoint_sha256"]
                        != checkpoint_sha256(previous_cp)):
                    raise ObserverJournalError("broken signed checkpoint chain")
            previous_cp = signed_cp
        if previous_cp is not None and checkpoint_sha256(previous_cp) != checkpoint_sha256(cp):
            raise ObserverJournalError("observer high-water disconnected from log")
        if pending:
            status = db.execute("SELECT status FROM events WHERE operation_id=?",
                                (pending[0],)).fetchone()[0]
            if status == "prepared" and db.execute(
                "SELECT generation FROM events WHERE operation_id=?",
                (pending[0],)
            ).fetchone()[0] != generation + 1:
                raise ObserverJournalError("prepared generation not current")
            if status == "signed_unconfirmed" and db.execute(
                "SELECT generation FROM events WHERE operation_id=?",
                (pending[0],)
            ).fetchone()[0] != generation:
                raise ObserverJournalError("unconfirmed ACK not at current high-water")
        return generation, cp, rows, pending

    @contextmanager
    def _transaction(self, *, write: bool = True):
        with self._lock:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                values = self._validate_db(db)
                yield db, values
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            finally:
                db.close()

    def state(self) -> dict:
        """Sanitized, non-authorizing local observer state."""
        with self._transaction(write=False) as (_, (gen, cp, rows, pending)):
            return {
                "schema": 1, "generation": gen,
                "state_sha256": cp["payload"]["state_sha256"],
                "checkpoint_sha256": checkpoint_sha256(cp),
                "unresolved": len(pending) != 0,
                "pending_operation_count": len(pending),
                "agent_dispatch_authorized": False,
                "observer_independence_verified": False,
                "rollback_resistance_verified": False,
                "decision": "HOLD",
            }

    def prepare(self, *, operation_id: str, request_sha256: str,
                before_state_sha256: str) -> dict:
        if (not OP.fullmatch(operation_id)
                or not HEX.fullmatch(request_sha256)
                or not HEX.fullmatch(before_state_sha256)):
            raise ObserverJournalError("invalid synthetic prepare arguments")
        with self._transaction() as (db, (generation, current, rows, pending)):
            if pending or current["payload"]["state_sha256"] != before_state_sha256:
                raise ObserverJournalError("observer fenced or worker not current")
            if db.execute("SELECT 1 FROM events WHERE operation_id=?",
                          (operation_id,)).fetchone():
                raise ObserverJournalError("duplicate operation")
            prepare = _signed(self.signer, {
                "schema": 1, "kind": "synthetic_prepare",
                "observer_id": self.observer_id, "worker_id": self.worker_id,
                "operation_id": operation_id,
                "request_sha256": request_sha256,
                "before_state_sha256": before_state_sha256,
                "generation": generation + 1,
                "previous_checkpoint_sha256": checkpoint_sha256(current),
            })
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)",
                (operation_id, request_sha256, before_state_sha256, None,
                 generation + 1, json.dumps(prepare, sort_keys=True),
                 None, None, "prepared"))
            return prepare

    def acknowledge(self, *, prepared: dict, after_state_sha256: str,
                    externally_observed_sha256: str) -> tuple[dict, dict]:
        if (not HEX.fullmatch(after_state_sha256)
                or after_state_sha256 != externally_observed_sha256):
            raise ObserverJournalError("unmatched external post-commit evidence")
        with self._transaction() as (db, (gen, current, rows, pending)):
            if (len(pending) != 1
                    or not isinstance(prepared, dict)
                    or "payload" not in prepared):
                raise ObserverJournalError("missing or unsafe prepare")
            op = prepared["payload"]["operation_id"]
            row = db.execute(
                "SELECT request_sha256,before_state,generation,prepare,status "
                "FROM events WHERE operation_id=?", (op,)).fetchone()
            if not row or row[4] != "prepared":
                raise ObserverJournalError("already signed, cancelled or missing")
            request_sha, before, target_gen, prepared_json, _ = row
            if (prepared != json.loads(prepared_json)
                    or before != current["payload"]["state_sha256"]
                    or target_gen != gen + 1
                    or after_state_sha256 == before):
                raise ObserverJournalError("non-sequential commit")
            new_cp = sign_fixture(
                observer_key=self.signer,
                observer_id=self.observer_id,
                worker_id=self.worker_id, state_sha256=after_state_sha256,
                generation=target_gen,
                previous_checkpoint_sha256=checkpoint_sha256(current),
                created_unix=1000 + target_gen)
            ack = _signed(self.signer, {
                **prepared["payload"],
                "kind": "synthetic_commit_ack",
                "after_state_sha256": after_state_sha256,
                "checkpoint_sha256": checkpoint_sha256(new_cp),
            })
            # Persist the new checkpoint + signed ACK + unresolved fence
            # atomically in ONE SQLite transaction before returning an ACK.
            db.execute(
                "UPDATE events SET after_state=?,checkpoint=?,ack=?,status=? "
                "WHERE operation_id=? AND status='prepared'",
                (after_state_sha256, json.dumps(new_cp, sort_keys=True),
                 json.dumps(ack, sort_keys=True), "signed_unconfirmed", op))
            db.execute("UPDATE metadata SET generation=?,checkpoint=? WHERE id=1",
                       (target_gen, json.dumps(new_cp, sort_keys=True)))
            return new_cp, ack

    def confirm_delivery(self, *, ack: dict,
                         observed_worker_sha256: str) -> None:
        """Cooperative test-only confirmation; NOT an independent source ACK."""
        if not isinstance(ack, dict) or not HEX.fullmatch(observed_worker_sha256):
            raise ObserverJournalError("invalid confirmation")
        with self._transaction() as (db, (gen, current, rows, pending)):
            if len(pending) != 1:
                raise ObserverJournalError("no fenced ACK to confirm")
            op = pending[0]
            row = db.execute(
                "SELECT ack,after_state,status FROM events WHERE operation_id=?",
                (op,)).fetchone()
            if (not row or row[2] != "signed_unconfirmed"
                    or json.loads(row[0]) != ack
                    or row[1] != observed_worker_sha256
                    or current["payload"]["state_sha256"] != observed_worker_sha256):
                raise ObserverJournalError("ACK mismatch or stale worker state")
            db.execute(
                "UPDATE events SET status='confirmed' WHERE operation_id=? "
                "AND status='signed_unconfirmed'", (op,))

    def abort_prepared(self, *, prepared: dict,
                       observed_worker_sha256: str) -> None:
        """Abort a PREPARE only if worker state was not changed."""
        with self._transaction() as (db, (gen, cp, rows, pending)):
            if len(pending) != 1:
                raise ObserverJournalError("no prepared operation")
            op = pending[0]
            row = db.execute(
                "SELECT status,prepare FROM events WHERE operation_id=?",
                (op,)).fetchone()
            if (not row or row[0] != "prepared"
                    or json.loads(row[1]) != prepared
                    or observed_worker_sha256 != cp["payload"]["state_sha256"]):
                raise ObserverJournalError("unsafe abort; operation remains fenced")
            db.execute(
                "UPDATE events SET status='aborted' WHERE operation_id=?", (op,))

    def uncertain_snapshot(self) -> dict:
        """No signed artifacts or bearer tokens exported by default."""
        return self.state()


if __name__ == "__main__":
    raise SystemExit("No live observer service or runtime authorization")
