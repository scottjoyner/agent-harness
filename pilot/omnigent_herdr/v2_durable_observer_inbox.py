#!/usr/bin/env python3
"""Single-operation, restart-durable SYNTHETIC V2 observer publication inbox.

No agent, provider, model or real resource access. Observer Ed25519 signer
and worker PUBLIC key are supplied by the fixture, NEVER stored as secrets.
A separate caller-supplied (revision, signed-marker SHA-256) is required
on EVERY inspection or transition. This detects an older DB ONLY if a
newer anchor survives outside it; independent custody is NOT provided.

This inbox binds one V2 outbox publication, observer ACK and worker
delivery receipt in sequential SQLite transactions, with signed observer
stage markers. It does NOT make those network effects distributed-atomic.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from capability_gate import _encode, _decode
from observer_checkpoint import ZERO, _json_bytes, public_fingerprint
from v2_outbox_publication import (
    publication_sha256, observer_verify_publication, observer_sign_ack,
    observer_verify_delivery, worker_verify_ack,
)

APP_ID = 0x4F475649  # OGVI, not legacy observer/lease/worker receipt DB
VERSION = 1
DOMAIN = b"herdr-v2-synthetic-durable-observer-inbox-v1\0"
MAX_DB_SIZE = 1024 * 1024
STAGES = (
    "empty", "publication_fenced", "ack_signed_fenced", "delivered",
    "quarantined_untrusted",
)
OP = re.compile(r"^[a-f0-9]{32}$")
MARK_FIELDS = {
    "schema", "kind", "observer_id", "worker_id",
    "observer_pin", "worker_pin", "revision", "stage",
    "operation_id", "publication_sha256", "ack_sha256",
    "delivery_sha256", "before_state_sha256", "after_state_sha256",
    "outbox_record_no", "outbox_record_sha256",
}


class UnsafeObserverInbox(ValueError):
    """An uncertain, changed, rolled-back or unsupported observer state."""


def _digest(value: dict) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _encode_db(value: dict) -> str:
    return _json_bytes(value).decode("ascii")


def _read_envelope(value: str | None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 8192:
        raise UnsafeObserverInbox("missing or oversized signed artifact")
    try:
        doc = json.loads(value)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise UnsafeObserverInbox("malformed stored artifact") from exc
    if not isinstance(doc, dict) or _encode_db(doc) != value:
        raise UnsafeObserverInbox("noncanonical or duplicate-key artifact")
    return doc


class SyntheticV2DurableObserverInbox:
    """One-operation proof, no release grant even in 'delivered' state."""

    def __init__(self, path: Path, *, observer_signer: Ed25519PrivateKey,
                 worker_public_key: Ed25519PublicKey,
                 observer_id: str, worker_id: str,
                 initial_state: str | None = None, create=False):
        self.path = Path(path)
        self.signer = observer_signer
        self.observer_public = observer_signer.public_key()
        self.worker_public = worker_public_key
        self.observer_pin = public_fingerprint(self.observer_public)
        self.worker_pin = public_fingerprint(worker_public_key)
        self.observer_id = observer_id
        self.worker_id = worker_id
        self.lock = threading.RLock()
        # Available only immediately after an exclusive fixture genesis.
        # Reopening NEVER derives an "authoritative" anchor from the DB.
        self.created_head: tuple[int, str] | None = None
        if (not isinstance(observer_id, str) or not isinstance(worker_id, str)
                or not observer_id or not worker_id
                or len(observer_id) > 96 or len(worker_id) > 96
                or not all(c.isascii() and (c.isalnum() or c in "._-")
                           for c in observer_id + worker_id)
                or observer_id == worker_id
                or self.observer_pin == self.worker_pin):
            raise UnsafeObserverInbox("invalid or overlapping signing roles")
        self._initialize(create=create, initial_state=initial_state)

    def _path(self, *, missing_ok=False):
        p = self.path
        if not p.is_absolute() or p.suffix != ".sqlite3":
            raise UnsafeObserverInbox("observer inbox requires absolute .sqlite3 path")
        parent = p.parent
        if not parent.is_dir() or parent.is_symlink():
            raise UnsafeObserverInbox("observer inbox parent is not a real directory")
        st = parent.stat()
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise UnsafeObserverInbox("observer inbox parent must be owner-private")
        try:
            data = p.lstat()
        except FileNotFoundError:
            if missing_ok:
                return False
            raise UnsafeObserverInbox("observer inbox missing; cannot reset")
        if (not stat.S_ISREG(data.st_mode) or data.st_nlink != 1
                or data.st_uid != os.getuid() or data.st_mode & 0o077):
            raise UnsafeObserverInbox("observer inbox must be owner-private regular file")
        return True

    def _connect(self):
        self._path()
        uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
        db = sqlite3.connect(uri, timeout=3, uri=True, isolation_level=None)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA busy_timeout=3000")
            if self.path.stat().st_size > MAX_DB_SIZE:
                raise UnsafeObserverInbox("inbox too large")
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise UnsafeObserverInbox("inbox corrupted")
            if (db.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                    or db.execute("PRAGMA user_version").fetchone()[0] != VERSION):
                raise UnsafeObserverInbox("wrong SQLite application or schema")
            return db
        except BaseException:
            db.close()
            raise

    def _mark(self, *, revision: int, stage: str, operation_id: str,
              publication_sha256: str, ack_sha256: str, delivery_sha256: str,
              before_state_sha256: str, after_state_sha256: str,
              outbox_record_no: int, outbox_record_sha256: str) -> dict:
        payload = {
            "schema": 1, "kind": "synthetic_v2_observer_inbox_marker",
            "observer_id": self.observer_id, "worker_id": self.worker_id,
            "observer_pin": self.observer_pin, "worker_pin": self.worker_pin,
            "revision": revision, "stage": stage,
            "operation_id": operation_id,
            "publication_sha256": publication_sha256,
            "ack_sha256": ack_sha256, "delivery_sha256": delivery_sha256,
            "before_state_sha256": before_state_sha256,
            "after_state_sha256": after_state_sha256,
            "outbox_record_no": outbox_record_no,
            "outbox_record_sha256": outbox_record_sha256,
        }
        return {
            "algorithm": "Ed25519", "payload": payload,
            "signature": _encode(
                self.signer.sign(DOMAIN + _json_bytes(payload))),
        }

    def _verify_mark(self, mark: object) -> bool:
        if (not isinstance(mark, dict)
                or set(mark) != {"algorithm", "payload", "signature"}
                or mark["algorithm"] != "Ed25519"
                or not isinstance(mark["payload"], dict)
                or set(mark["payload"]) != MARK_FIELDS):
            return False
        p = mark["payload"]
        if (type(p["schema"]) is not int or p["schema"] != 1
                or p["kind"] != "synthetic_v2_observer_inbox_marker"
                or p["observer_id"] != self.observer_id
                or p["worker_id"] != self.worker_id
                or p["observer_pin"] != self.observer_pin
                or p["worker_pin"] != self.worker_pin
                or type(p["revision"]) is not int or p["revision"] not in range(5)
                or p["stage"] != STAGES[p["revision"]]):
            return False
        try:
            self.observer_public.verify(
                _decode(mark["signature"]), DOMAIN + _json_bytes(p))
            return True
        except (ValueError, TypeError, UnicodeError, InvalidSignature):
            return False

    def _initialize(self, *, create: bool, initial_state: str | None):
        found = self._path(missing_ok=create)
        if create:
            if found:
                raise UnsafeObserverInbox("existing inbox must not be overwritten")
            if (not isinstance(initial_state, str) or len(initial_state) != 64
                    or any(c not in "0123456789abcdef" for c in initial_state)):
                raise UnsafeObserverInbox("genesis state digest required")
            fd = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
            try:
                db = sqlite3.connect(str(self.path), isolation_level=None)
                try:
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute("PRAGMA journal_mode=DELETE")
                    db.execute(f"PRAGMA application_id={APP_ID}")
                    db.execute(f"PRAGMA user_version={VERSION}")
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("""CREATE TABLE metadata (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        observer_id TEXT NOT NULL,
                        worker_id TEXT NOT NULL,
                        observer_pin TEXT NOT NULL,
                        worker_pin TEXT NOT NULL,
                        initial_state TEXT NOT NULL,
                        marker TEXT NOT NULL
                    )""")
                    db.execute("""CREATE TABLE journal (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        publication TEXT NOT NULL,
                        signed_ack TEXT,
                        signed_delivery TEXT
                    )""")
                    mark = self._mark(
                        revision=0, stage="empty", operation_id="",
                        publication_sha256=ZERO, ack_sha256=ZERO,
                        delivery_sha256=ZERO,
                        before_state_sha256=initial_state,
                        after_state_sha256=initial_state,
                        outbox_record_no=0, outbox_record_sha256=ZERO)
                    db.execute(
                        "INSERT INTO metadata VALUES (1,?,?,?,?,?,?)",
                        (self.observer_id, self.worker_id,
                         self.observer_pin, self.worker_pin,
                         initial_state, _encode_db(mark)))
                    db.execute("COMMIT")
                    self.created_head = (0, _digest(mark))
                finally:
                    db.close()
            except BaseException:
                self.path.unlink(missing_ok=True)
                raise
        with self._transaction(write=False):
            pass

    def _validate(self, db):
        rows = db.execute(
            "SELECT observer_id,worker_id,observer_pin,worker_pin,"
            "initial_state,marker FROM metadata WHERE id=1"
        ).fetchall()
        if len(rows) != 1 or db.execute(
                "SELECT count(*) FROM metadata").fetchone()[0] != 1:
            raise UnsafeObserverInbox("invalid metadata cardinality")
        obs, worker, obs_pin, worker_pin, initial, signed_mark = rows[0]
        if ((obs, worker, obs_pin, worker_pin) != (
                self.observer_id, self.worker_id,
                self.observer_pin, self.worker_pin)
                or not isinstance(initial, str) or len(initial) != 64
                or any(c not in "0123456789abcdef" for c in initial)):
            raise UnsafeObserverInbox("observer role or baseline mismatch")
        mark = _read_envelope(signed_mark)
        if not self._verify_mark(mark):
            raise UnsafeObserverInbox("untrusted observer stage marker")
        p = mark["payload"]
        journal_rows = db.execute(
            "SELECT publication,signed_ack,signed_delivery "
            "FROM journal WHERE id=1"
        ).fetchall()
        if db.execute("SELECT count(*) FROM journal").fetchone()[0] > 1:
            raise UnsafeObserverInbox("duplicate journal operation")
        if p["revision"] == 4:
            # An invalid claimed publication MAY follow a real committed
            # debit. Persist a SIGNED quarantine marker even though no
            # worker publication can be trusted. There is deliberately
            # no ACK, receive retry, auto-unfence or recovery API.
            if (journal_rows
                    or not isinstance(p["operation_id"], str)
                    or OP.fullmatch(p["operation_id"]) is None
                    or not isinstance(p["publication_sha256"], str)
                    or len(p["publication_sha256"]) != 64
                    or any(c not in "0123456789abcdef"
                           for c in p["publication_sha256"])
                    or p["publication_sha256"] == ZERO
                    or any(p[k] != ZERO for k in (
                        "ack_sha256", "delivery_sha256",
                        "outbox_record_sha256"))
                    or p["before_state_sha256"] != initial
                    or p["after_state_sha256"] != initial
                    or p["outbox_record_no"] != 0):
                raise UnsafeObserverInbox("invalid untrusted-publication quarantine")
            return mark, None, None, None
        if p["revision"] == 0:
            if (journal_rows
                    or p["operation_id"] != ""
                    or any(p[k] != ZERO for k in (
                        "publication_sha256", "ack_sha256", "delivery_sha256",
                        "outbox_record_sha256"))
                    or p["before_state_sha256"] != initial
                    or p["after_state_sha256"] != initial
                    or p["outbox_record_no"] != 0):
                raise UnsafeObserverInbox("invalid synthetic inbox genesis")
            return mark, None, None, None
        if len(journal_rows) != 1:
            raise UnsafeObserverInbox("missing durable publication")
        pub = _read_envelope(journal_rows[0][0])
        ack = _read_envelope(journal_rows[0][1])
        delivery = _read_envelope(journal_rows[0][2])
        if pub is None:
            raise UnsafeObserverInbox("missing signed publication")
        record = pub.get("payload")
        if not isinstance(record, dict) or not isinstance(record.get("outbox_event"), dict):
            raise UnsafeObserverInbox("malformed publication event")
        e = record["outbox_event"]
        if (not observer_verify_publication(
                envelope=pub, worker_public_key=self.worker_public,
                trusted_worker_fingerprint=self.worker_pin,
                trusted_observer_fingerprint=self.observer_pin,
                expected_worker_id=self.worker_id,
                expected_observer_id=self.observer_id,
                expected_operation_id=p["operation_id"],
                expected_outbox_head=(
                    p["outbox_record_no"], p["outbox_record_sha256"]),
                expected_before_state=initial,
                expected_after_state=p["after_state_sha256"])
                or p["outbox_record_no"] != 1
                or e["previous_record_sha256"] != ZERO
                or p["before_state_sha256"] != initial
                or p["publication_sha256"] != publication_sha256(pub)):
            raise UnsafeObserverInbox("publication signature, scope or hash mismatch")
        if p["revision"] == 1:
            if ack is not None or delivery is not None or any(
                    p[k] != ZERO for k in ("ack_sha256", "delivery_sha256")):
                raise UnsafeObserverInbox("premature ACK/delivery state")
        else:
            if (ack is None
                    or not worker_verify_ack(
                        envelope=ack,
                        observer_public_key=self.observer_public,
                        trusted_observer_fingerprint=self.observer_pin,
                        publication=pub, expected_generation=2,
                        previous_checkpoint_sha256=ZERO)
                    or p["ack_sha256"] != publication_sha256(ack)):
                raise UnsafeObserverInbox("unverified or missing persisted ACK")
            if p["revision"] == 2:
                if delivery is not None or p["delivery_sha256"] != ZERO:
                    raise UnsafeObserverInbox("unexpected delivery in pending state")
            else:
                if (delivery is None
                        or not observer_verify_delivery(
                            envelope=delivery,
                            worker_public_key=self.worker_public,
                            trusted_worker_fingerprint=self.worker_pin,
                            trusted_observer_fingerprint=self.observer_pin,
                            publication=pub, observer_ack=ack)
                        or p["delivery_sha256"] != publication_sha256(delivery)):
                    raise UnsafeObserverInbox("unverified delivery receipt")
        return mark, pub, ack, delivery

    @contextmanager
    def _transaction(self, *, write=True):
        with self.lock:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                state = self._validate(db)
                yield db, state
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            finally:
                db.close()

    @staticmethod
    def _check_anchor(mark: dict, anchor: tuple[int, str]):
        actual = (mark["payload"]["revision"], _digest(mark))
        if (not isinstance(anchor, tuple) or len(anchor) != 2
                or type(anchor[0]) is not int or not isinstance(anchor[1], str)
                or anchor != actual):
            raise UnsafeObserverInbox("missing, stale or rolled-back observer anchor")

    def _save(self, db, marker: dict):
        db.execute("UPDATE metadata SET marker=? WHERE id=1",
                   (_encode_db(marker),))

    def inspect(self, *, trusted_anchor: tuple[int, str]) -> dict:
        with self._transaction(write=False) as (_, (mark, pub, ack, delivery)):
            self._check_anchor(mark, trusted_anchor)
            p = mark["payload"]
            return {
                "schema": 1, "mode": "synthetic_v2_durable_observer_inbox",
                "stage": p["stage"], "revision": p["revision"],
                "head_sha256": _digest(mark),
                "operation_id": p["operation_id"],
                "uncertain": p["stage"] in (
                    "publication_fenced", "ack_signed_fenced",
                    "quarantined_untrusted"),
                "signed_publication_present": pub is not None,
                "signed_ack_present": ack is not None,
                "signed_delivery_present": delivery is not None,
                "independent_worker_pin_verified": False,
                "external_anchor_custody_verified": False,
                "source_transaction_attested": False,
                "rollback_resistance_verified": False,
                "agent_dispatch_authorized": False,
                "model_egress_authorized": False,
                "provider_requests_forwarded": 0,
                "decision": "HOLD",
            }

    def quarantine_unverified(
            self, *, claimed_publication: object,
            expected_operation_id: str,
            trusted_anchor: tuple[int, str]) -> tuple[int, str]:
        """Operator/fixture-directed permanent quarantine of suspect effects.

        An invalid worker signature is NOT proof no debit occurred.
        This persists an observer-signed, bounded SHA-256 commitment to
        the untrusted claim, without recording it as an authenticated
        worker publication.  One suspicious claim permanently fences the
        research operation; no automatic retry, refund or ACK is allowed.
        """
        if (not isinstance(expected_operation_id, str)
                or OP.fullmatch(expected_operation_id) is None):
            raise UnsafeObserverInbox("quarantine requires exact operation scope")
        try:
            raw = _json_bytes(claimed_publication)
        except (ValueError, TypeError, UnicodeError) as exc:
            raise UnsafeObserverInbox("unserializable suspicious publication") from exc
        if len(raw) > 8192:
            raise UnsafeObserverInbox("oversized suspicious publication")
        digest = hashlib.sha256(raw).hexdigest()
        with self._transaction() as (db, (mark, _pub, _ack, _delivery)):
            self._check_anchor(mark, trusted_anchor)
            p = mark["payload"]
            if p["revision"] != 0:
                raise UnsafeObserverInbox("cannot replace existing observer history")
            quarantined = self._mark(
                revision=4, stage=STAGES[4],
                operation_id=expected_operation_id,
                publication_sha256=digest,
                ack_sha256=ZERO, delivery_sha256=ZERO,
                before_state_sha256=p["before_state_sha256"],
                after_state_sha256=p["before_state_sha256"],
                outbox_record_no=0, outbox_record_sha256=ZERO)
            self._save(db, quarantined)
            return (4, _digest(quarantined))

    def receive(self, *, publication: dict, trusted_anchor: tuple[int, str],
                expected_operation_id: str,
                expected_outbox_head: tuple[int, str],
                expected_after_state: str) -> tuple[int, str]:
        with self._transaction() as (db, (mark, _pub, _ack, _delivery)):
            self._check_anchor(mark, trusted_anchor)
            p = mark["payload"]
            if p["revision"] != 0:
                raise UnsafeObserverInbox("observer already has an operation")
            if not observer_verify_publication(
                envelope=publication, worker_public_key=self.worker_public,
                trusted_worker_fingerprint=self.worker_pin,
                trusted_observer_fingerprint=self.observer_pin,
                expected_worker_id=self.worker_id,
                expected_observer_id=self.observer_id,
                expected_operation_id=expected_operation_id,
                expected_outbox_head=expected_outbox_head,
                expected_before_state=p["before_state_sha256"],
                expected_after_state=expected_after_state):
                raise UnsafeObserverInbox("invalid signed worker outbox publication")
            event = publication["payload"]["outbox_event"]
            if (expected_outbox_head[0] != 1
                    or event["previous_record_sha256"] != ZERO):
                raise UnsafeObserverInbox("only first synthetic V2 event supported")
            nextmark = self._mark(
                revision=1, stage=STAGES[1],
                operation_id=expected_operation_id,
                publication_sha256=publication_sha256(publication),
                ack_sha256=ZERO, delivery_sha256=ZERO,
                before_state_sha256=p["before_state_sha256"],
                after_state_sha256=expected_after_state,
                outbox_record_no=expected_outbox_head[0],
                outbox_record_sha256=expected_outbox_head[1])
            db.execute(
                "INSERT INTO journal VALUES (1,?,?,?)",
                (_encode_db(publication), None, None))
            self._save(db, nextmark)
            return (1, _digest(nextmark))

    def issue_ack(self, *, trusted_anchor: tuple[int, str]) -> tuple[tuple[int, str], dict]:
        with self._transaction() as (db, (mark, pub, _ack, _delivery)):
            self._check_anchor(mark, trusted_anchor)
            p = mark["payload"]
            if p["revision"] != 1 or pub is None:
                raise UnsafeObserverInbox("no pending verified publication to ACK")
            ack = observer_sign_ack(
                signer=self.signer, publication=pub,
                worker_public_key=self.worker_public,
                trusted_worker_fingerprint=self.worker_pin,
                expected_worker_id=self.worker_id,
                expected_observer_id=self.observer_id,
                expected_operation_id=p["operation_id"],
                expected_outbox_head=(
                    p["outbox_record_no"], p["outbox_record_sha256"]),
                expected_before_state=p["before_state_sha256"],
                expected_after_state=p["after_state_sha256"],
                observer_generation=2, previous_checkpoint_sha256=ZERO)
            nextmark = self._mark(
                revision=2, stage=STAGES[2],
                operation_id=p["operation_id"],
                publication_sha256=p["publication_sha256"],
                ack_sha256=publication_sha256(ack),
                delivery_sha256=ZERO,
                before_state_sha256=p["before_state_sha256"],
                after_state_sha256=p["after_state_sha256"],
                outbox_record_no=p["outbox_record_no"],
                outbox_record_sha256=p["outbox_record_sha256"])
            db.execute("UPDATE journal SET signed_ack=? WHERE id=1",
                       (_encode_db(ack),))
            self._save(db, nextmark)
            # Return signed ACK only after SQLite transaction commits;
            # do not reissue after lost response, even if same op ID.
            outcome = ((2, _digest(nextmark)), ack)
        return outcome

    def confirm(self, *, delivery: dict,
                trusted_anchor: tuple[int, str]) -> tuple[int, str]:
        with self._transaction() as (db, (mark, pub, ack, _delivery)):
            self._check_anchor(mark, trusted_anchor)
            p = mark["payload"]
            if p["revision"] != 2 or pub is None or ack is None:
                raise UnsafeObserverInbox("no signed pending ACK to confirm")
            if not observer_verify_delivery(
                    envelope=delivery, worker_public_key=self.worker_public,
                    trusted_worker_fingerprint=self.worker_pin,
                    trusted_observer_fingerprint=self.observer_pin,
                    publication=pub, observer_ack=ack):
                raise UnsafeObserverInbox("invalid, stale or forged delivery receipt")
            nextmark = self._mark(
                revision=3, stage=STAGES[3],
                operation_id=p["operation_id"],
                publication_sha256=p["publication_sha256"],
                ack_sha256=p["ack_sha256"],
                delivery_sha256=publication_sha256(delivery),
                before_state_sha256=p["before_state_sha256"],
                after_state_sha256=p["after_state_sha256"],
                outbox_record_no=p["outbox_record_no"],
                outbox_record_sha256=p["outbox_record_sha256"])
            db.execute("UPDATE journal SET signed_delivery=? WHERE id=1",
                       (_encode_db(delivery),))
            self._save(db, nextmark)
            return (3, _digest(nextmark))


if __name__ == "__main__":
    raise SystemExit("Research-only durable observer inbox, no service started")
