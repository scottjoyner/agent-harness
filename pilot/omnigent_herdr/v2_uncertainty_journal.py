#!/usr/bin/env python3
"""Research-only bounded, restart-durable, deny-only worker recovery history.

Stores the original WORKER-SIGNED intent and all SOURCE read-only replies,
re-verifies each classification on every reopen, and never offers a retry,
refund, dispatch, or fence-release operation. Data lives solely in one
disposable owner-private SQLite fixture root. Same-admin rollback of BOTH
DB and caller-held head remains possible and is explicitly tested.
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

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from observer_checkpoint import ZERO, _json_bytes, public_fingerprint
from v2_cross_host_uncertainty import BASE, classify_source_recovery
from v2_source_transaction_fixture import SHA_RE, OP_RE
from v2_outbox_publication import observer_verify_publication

ROOT_RE = re.compile(r"^og-v2-uncertain-[A-Za-z0-9_]{6,8}$")
APP_ID = 0x4F47554A  # OGUJ - distinct from all other V2 SQLite stores
VERSION = 1
MAX_DB_BYTES = 1024 * 1024
MAX_EVENTS = 4
MAX_JSON = 16384
CTX_KEYS = {
    "schema", "kind", "operation_id", "worker_public_base64",
    "worker_pin", "observer_pin", "source_public_base64", "source_pin",
    "worker_id", "observer_id", "worker_outbox_head",
    "worker_before_state", "worker_after_state", "prior_source_head",
}
STATES = {
    "UNCERTAIN_HOLD", "QUARANTINED_HOLD", "SOURCE_LOCAL_COMMITTED_HOLD",
}


class UnsafeUncertaintyJournal(ValueError):
    """Untrusted, malformed, rolled-back or conflicting local HOLD evidence."""


def _digest(data: object) -> str:
    return hashlib.sha256(_json_bytes(data)).hexdigest()


def _canonical(data: object) -> str:
    raw = _json_bytes(data)
    if len(raw) > MAX_JSON:
        raise UnsafeUncertaintyJournal("oversized journal evidence")
    return raw.decode("ascii")


def _unique(pairs):
    d = {}
    for k, value in pairs:
        if k in d:
            raise UnsafeUncertaintyJournal("duplicate journal JSON field")
        d[k] = value
    return d


def _load(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw) > MAX_JSON:
        raise UnsafeUncertaintyJournal("oversized journal JSON record")
    try:
        data = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(data, dict) or _canonical(data) != raw:
            raise UnsafeUncertaintyJournal("noncanonical journal record")
        return data
    except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
        raise UnsafeUncertaintyJournal("invalid persisted journal JSON") from exc


def _pub(encoded: str) -> Ed25519PublicKey:
    if not isinstance(encoded, str) or len(encoded) != 44:
        raise UnsafeUncertaintyJournal("invalid public key encoding")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != encoded:
            raise UnsafeUncertaintyJournal("noncanonical public key encoding")
        return Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise UnsafeUncertaintyJournal("bad public key") from exc


def _context(ctx: object) -> dict:
    if not isinstance(ctx, dict) or set(ctx) != CTX_KEYS:
        raise UnsafeUncertaintyJournal("unrecognized recovery context")
    if (type(ctx["schema"]) is not int or ctx["schema"] != 1
            or ctx["kind"] != "synthetic_v2_uncertain_worker_intent"
            or not isinstance(ctx["operation_id"], str)
            or OP_RE.fullmatch(ctx["operation_id"]) is None
            or not all(isinstance(ctx[k], str) and SHA_RE.fullmatch(ctx[k])
                       for k in ("worker_pin", "observer_pin", "source_pin",
                                 "worker_before_state", "worker_after_state"))
            or not all(isinstance(ctx[k], str) and
                       re.fullmatch(r"[a-zA-Z0-9_.:-]{1,96}", ctx[k])
                       for k in ("worker_id", "observer_id"))
            or not all(isinstance(ctx[k], list) and len(ctx[k]) == 2
                       and type(ctx[k][0]) is int
                       and isinstance(ctx[k][1], str)
                       and SHA_RE.fullmatch(ctx[k][1]) is not None
                       for k in ("worker_outbox_head", "prior_source_head"))):
        raise UnsafeUncertaintyJournal("incorrect recovery context shape")
    if not (1 <= ctx["worker_outbox_head"][0] <= 128
            and 0 <= ctx["prior_source_head"][0] < 2):
        raise UnsafeUncertaintyJournal("context head outside synthetic range")
    if (public_fingerprint(_pub(ctx["worker_public_base64"]))
            != ctx["worker_pin"]
            or public_fingerprint(_pub(ctx["source_public_base64"]))
            != ctx["source_pin"]):
        raise UnsafeUncertaintyJournal("public signer pins don't match context")
    return ctx


def _classifier_args(ctx: dict, signed_worker: dict) -> dict:
    return {
        "worker_publication": signed_worker,
        "worker_public_key": _pub(ctx["worker_public_base64"]),
        "trusted_worker_pin": ctx["worker_pin"],
        "trusted_observer_pin": ctx["observer_pin"],
        "expected_worker_id": ctx["worker_id"],
        "expected_observer_id": ctx["observer_id"],
        "expected_operation_id": ctx["operation_id"],
        "expected_worker_outbox_head": tuple(ctx["worker_outbox_head"]),
        "expected_worker_before_state": ctx["worker_before_state"],
        "expected_worker_after_state": ctx["worker_after_state"],
        "source_public_key": _pub(ctx["source_public_base64"]),
        "trusted_source_pin": ctx["source_pin"],
        "trusted_prior_source_head": tuple(ctx["prior_source_head"]),
    }


def context_digest(ctx: dict) -> str:
    _context(ctx)
    return _digest(ctx)


def _root(root: Path):
    if (not root.is_absolute() or root.parent != Path.home() / ".cache"
            or ROOT_RE.fullmatch(root.name) is None
            or root.is_symlink() or not root.is_dir()):
        raise UnsafeUncertaintyJournal("not a disposable uncertainty root")
    s = root.stat()
    if s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise UnsafeUncertaintyJournal("research root is not private")


class SyntheticWorkerUncertaintyJournal:
    """One bounded operator-held operation, no resident API or dispatch."""

    def __init__(self, root: Path, *, create=False,
                 context: dict | None = None,
                 worker_publication: dict | None = None):
        self.root = Path(root)
        _root(self.root)
        self.path = self.root / "worker-uncertainty.sqlite3"
        if create:
            if context is None or worker_publication is None:
                raise UnsafeUncertaintyJournal("missing signed genesis intent")
            self._create(context, worker_publication)

    def _safe_db(self):
        s = self.path.lstat()
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1
                or s.st_uid != os.getuid() or s.st_mode & 0o077
                or s.st_size < 1 or s.st_size > MAX_DB_BYTES):
            raise UnsafeUncertaintyJournal("unsafe or missing uncertainty DB")

    def _connect(self):
        _root(self.root)
        self._safe_db()
        uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
        db = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=4)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=4000")
            if (db.execute("PRAGMA integrity_check").fetchone() != ("ok",)
                    or db.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                    or db.execute("PRAGMA user_version").fetchone()[0] != VERSION):
                raise UnsafeUncertaintyJournal("damaged or foreign journal schema")
            return db
        except BaseException:
            db.close()
            raise

    def _create(self, context, worker_publication):
        ctx = _context(context)
        if not isinstance(worker_publication, dict):
            raise UnsafeUncertaintyJournal("missing original worker publication")
        try:
            signature_ok = observer_verify_publication(
                envelope=worker_publication,
                worker_public_key=_pub(ctx["worker_public_base64"]),
                trusted_worker_fingerprint=ctx["worker_pin"],
                trusted_observer_fingerprint=ctx["observer_pin"],
                expected_worker_id=ctx["worker_id"],
                expected_observer_id=ctx["observer_id"],
                expected_operation_id=ctx["operation_id"],
                expected_outbox_head=tuple(ctx["worker_outbox_head"]),
                expected_before_state=ctx["worker_before_state"],
                expected_after_state=ctx["worker_after_state"])
        except (TypeError, ValueError, KeyError, AttributeError):
            signature_ok = False
        if not signature_ok:
            raise UnsafeUncertaintyJournal("original worker intent signature invalid")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        db = sqlite3.connect(str(self.path), isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(f"PRAGMA application_id={APP_ID}")
            db.execute(f"PRAGMA user_version={VERSION}")
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE source_intent (
                id INTEGER PRIMARY KEY CHECK(id=1),
                context_json TEXT NOT NULL,
                signed_worker_json TEXT NOT NULL,
                context_sha256 TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE observations (
                ordinal INTEGER PRIMARY KEY,
                source_reply_json TEXT NOT NULL,
                verdict_json TEXT NOT NULL,
                previous_sha256 TEXT NOT NULL,
                record_sha256 TEXT NOT NULL
            )""")
            db.execute("INSERT INTO source_intent VALUES (1,?,?,?)", (
                _canonical(ctx), _canonical(worker_publication),
                _digest(ctx)))
            db.execute("COMMIT")
        finally:
            db.close()
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _validate(self, db, expected_context_sha256: str):
        records = db.execute(
            "SELECT context_json,signed_worker_json,context_sha256 "
            "FROM source_intent").fetchall()
        if len(records) != 1:
            raise UnsafeUncertaintyJournal("missing or duplicated genesis")
        ctx = _load(records[0][0])
        _context(ctx)
        publication = _load(records[0][1])
        if (not isinstance(expected_context_sha256, str)
                or SHA_RE.fullmatch(expected_context_sha256) is None
                or _digest(ctx) != records[0][2]
                or expected_context_sha256 != records[0][2]):
            raise UnsafeUncertaintyJournal("context missing/changed vs pinned head")
        args = _classifier_args(ctx, publication)
        try:
            signer_ok = observer_verify_publication(
                envelope=publication,
                worker_public_key=args["worker_public_key"],
                trusted_worker_fingerprint=args["trusted_worker_pin"],
                trusted_observer_fingerprint=args["trusted_observer_pin"],
                expected_worker_id=args["expected_worker_id"],
                expected_observer_id=args["expected_observer_id"],
                expected_operation_id=args["expected_operation_id"],
                expected_outbox_head=args["expected_worker_outbox_head"],
                expected_before_state=args["expected_worker_before_state"],
                expected_after_state=args["expected_worker_after_state"])
        except (ValueError, TypeError, KeyError, AttributeError):
            signer_ok = False
        if not signer_ok:
            raise UnsafeUncertaintyJournal("worker signature no longer verifies")
        observations = db.execute(
            "SELECT ordinal,source_reply_json,verdict_json,"
            "previous_sha256,record_sha256 FROM observations ORDER BY ordinal"
        ).fetchall()
        if len(observations) > MAX_EVENTS:
            raise UnsafeUncertaintyJournal("too many recovery observations")
        previous = _digest(ctx)
        status = "UNCERTAIN_HOLD"
        for n, (ordinal, reply_json, verdict_json, prior_sha, head) in enumerate(
                observations, 1):
            if type(ordinal) is not int or ordinal != n or prior_sha != previous:
                raise UnsafeUncertaintyJournal("reordered/forked observation chain")
            raw_reply = _load(reply_json)
            if set(raw_reply) != {"reply"}:
                raise UnsafeUncertaintyJournal("invalid recovery reply wrapper")
            observed = classify_source_recovery(
                source_reply=raw_reply["reply"], **args)
            saved = _load(verdict_json)
            if saved != observed or saved.get("status") not in STATES:
                raise UnsafeUncertaintyJournal("stored verdict no longer verifies")
            if (status == "QUARANTINED_HOLD"
                    or status == "SOURCE_LOCAL_COMMITTED_HOLD"
                       and observed["status"] != "SOURCE_LOCAL_COMMITTED_HOLD"):
                raise UnsafeUncertaintyJournal("illegal hold-state downgrade")
            if (status == "SOURCE_LOCAL_COMMITTED_HOLD"
                    and observed["status"] == "SOURCE_LOCAL_COMMITTED_HOLD"
                    and n > 1):
                # Never silently allow a different, second source
                # receipt under the same committed operation.
                prior = _load(observations[n - 2][1])
                if prior != raw_reply:
                    raise UnsafeUncertaintyJournal("conflicting source receipts")
            digest = _digest({
                "ordinal": n, "reply": raw_reply,
                "verdict": saved, "previous_sha256": previous,
            })
            if head != digest:
                raise UnsafeUncertaintyJournal("observation SHA chain tampered")
            status = saved["status"]
            previous = digest
        return ctx, publication, args, (len(observations), previous), status

    def observe(self, source_reply: object, *, expected_context_sha256: str,
                trusted_prior_journal_head: tuple[int, str]) -> dict:
        """One durable HOLD-classification record; no source request or retry."""
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            ctx, pub, args, head, status = self._validate(
                db, expected_context_sha256)
            if (not isinstance(trusted_prior_journal_head, tuple)
                    or len(trusted_prior_journal_head) != 2
                    or type(trusted_prior_journal_head[0]) is not int
                    or head != trusted_prior_journal_head
                    or head[0] >= MAX_EVENTS
                    or status == "QUARANTINED_HOLD"):
                raise UnsafeUncertaintyJournal("stale/full/terminal HOLD journal")
            verdict = classify_source_recovery(
                source_reply=source_reply, **args)
            # A validated source-local commit can only be observed
            # again identically; all contradictory evidence stays HOLD
            # and cannot rewrite or downgrade its logged history.
            if status == "SOURCE_LOCAL_COMMITTED_HOLD":
                if verdict["status"] != "SOURCE_LOCAL_COMMITTED_HOLD":
                    raise UnsafeUncertaintyJournal("source evidence conflict")
                old = db.execute(
                    "SELECT source_reply_json FROM observations "
                    "ORDER BY ordinal DESC LIMIT 1").fetchone()
                if not old or _load(old[0]) != {"reply": source_reply}:
                    raise UnsafeUncertaintyJournal("new source receipt after commit")
            number = head[0] + 1
            wrapper = {"reply": source_reply}
            item = {
                "ordinal": number, "reply": wrapper,
                "verdict": verdict, "previous_sha256": head[1],
            }
            record_sha = _digest(item)
            db.execute("INSERT INTO observations VALUES (?,?,?,?,?)", (
                number, _canonical(wrapper), _canonical(verdict), head[1],
                record_sha))
            db.execute("COMMIT")
            return self._response(
                verdict, (number, record_sha), expected_context_sha256)
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def reconcile_lost_observation_reply(
            self, *, source_reply: object,
            expected_context_sha256: str,
            trusted_prior_journal_head: tuple[int, str]) -> dict:
        """Strict, read-only check for a lost journal append ACK.

        Caller supplies the *original exact observation* and separately
        retained prior journal head. A successful result proves only that
        this ONE observation follows that head in the current local SQLite
        image. It does not create or reissue any debit/lease/agent work.
        If the write never committed, evidence is missing: caller HOLDs,
        rather than automatically retrying a local or remote write.
        """
        db = self._connect()
        try:
            db.execute("BEGIN")
            ctx, publication, args, latest, status = self._validate(
                db, expected_context_sha256)
            if (not isinstance(trusted_prior_journal_head, tuple)
                    or len(trusted_prior_journal_head) != 2
                    or type(trusted_prior_journal_head[0]) is not int
                    or not isinstance(trusted_prior_journal_head[1], str)
                    or SHA_RE.fullmatch(trusted_prior_journal_head[1]) is None
                    or latest[0] != trusted_prior_journal_head[0] + 1
                    or not 1 <= latest[0] <= MAX_EVENTS):
                raise UnsafeUncertaintyJournal(
                    "lost-reply recovery is not exactly next observation")
            last = db.execute(
                "SELECT source_reply_json,previous_sha256 FROM observations "
                "WHERE ordinal=?", (latest[0],)).fetchone()
            if (not last or last[1] != trusted_prior_journal_head[1]
                    or _load(last[0]) != {"reply": source_reply}):
                raise UnsafeUncertaintyJournal(
                    "source observation differs from persisted signed evidence")
            # _validate() above recomputes original worker and source
            # signatures, saved verdicts, and EVERY hash-chain link.
            # Comparison here binds the replayed source evidence exactly.
            db.execute("COMMIT")
            return self._response(
                {"status": status,
                 "source_local_commit_verified":
                     status == "SOURCE_LOCAL_COMMITTED_HOLD"},
                latest, expected_context_sha256)
        finally:
            db.close()

    def inspect(self, *, expected_context_sha256: str,
                trusted_journal_head: tuple[int, str]) -> dict:
        """Exact-head readback from NEW process. Always denies dispatch."""
        db = self._connect()
        try:
            db.execute("BEGIN")
            ctx, pub, args, actual, status = self._validate(
                db, expected_context_sha256)
            if (not isinstance(trusted_journal_head, tuple)
                    or len(trusted_journal_head) != 2
                    or type(trusted_journal_head[0]) is not int
                    or actual != trusted_journal_head):
                raise UnsafeUncertaintyJournal("caller does not retain latest head")
            db.execute("COMMIT")
            return self._response(
                {"status": status,
                 "source_local_commit_verified":
                     status == "SOURCE_LOCAL_COMMITTED_HOLD"},
                actual, expected_context_sha256)
        finally:
            db.close()

    @staticmethod
    def _response(verdict: dict, head: tuple[int, str],
                  context_sha256: str) -> dict:
        return {
            **BASE, "mode": "synthetic_v2_durable_uncertainty_journal",
            "status": verdict["status"],
            "source_local_commit_verified":
                verdict["source_local_commit_verified"],
            "journal_head": list(head),
            "context_sha256": context_sha256,
        }


def _cli() -> int:
    """One-shot scratch-only journal, with controlled process exit windows."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--command", choices=("inspect", "observe", "reconcile"),
                        required=True)
    parser.add_argument("--context-sha256", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument(
        "--research-fault",
        choices=("none", "crash-before-journal-commit",
                 "crash-after-journal-commit"),
        default="none",
        help="Fault applies ONLY to a disposable journal observe process.")
    args = parser.parse_args()
    try:
        parts = args.head.split(":")
        if (len(parts) != 2 or not re.fullmatch(r"[0-4]", parts[0])
                or SHA_RE.fullmatch(parts[1]) is None):
            raise UnsafeUncertaintyJournal("invalid pinned journal head")
        head = (int(parts[0]), parts[1])
        if (args.research_fault != "none" and args.command != "observe"):
            raise UnsafeUncertaintyJournal("fault flag denied for readback")
        journal = SyntheticWorkerUncertaintyJournal(args.root)
        if args.command == "inspect":
            result = journal.inspect(
                expected_context_sha256=args.context_sha256,
                trusted_journal_head=head)
        else:
            raw = sys.stdin.buffer.readline(MAX_JSON + 1)
            if not raw or len(raw) > MAX_JSON:
                raise UnsafeUncertaintyJournal(
                    "missing or oversized journal observation")
            request = json.loads(raw, object_pairs_hook=_unique)
            if not isinstance(request, dict) or set(request) != {"source_reply"}:
                raise UnsafeUncertaintyJournal("unexpected journal observation")
            reply = request["source_reply"]
            if args.command == "reconcile":
                result = journal.reconcile_lost_observation_reply(
                    source_reply=reply,
                    expected_context_sha256=args.context_sha256,
                    trusted_prior_journal_head=head)
            else:
                if args.research_fault == "crash-before-journal-commit":
                    # Exit only THIS authorized disposable CLI process,
                    # before performing any worker journal write.
                    os._exit(96)
                result = journal.observe(
                    reply, expected_context_sha256=args.context_sha256,
                    trusted_prior_journal_head=head)
                if args.research_fault == "crash-after-journal-commit":
                    # Local SQLite commit already returned. Deliberately
                    # suppress the ACK to exercise read-only reconciliation.
                    os._exit(97)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (UnsafeUncertaintyJournal, sqlite3.Error, OSError,
            ValueError, TypeError, KeyError, AttributeError,
            UnicodeError, OverflowError):
        print(json.dumps({
            **BASE, "mode": "synthetic_v2_durable_uncertainty_journal",
            "status": "QUARANTINED_HOLD",
            "source_local_commit_verified": False,
            "error": "research journal operation denied",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
