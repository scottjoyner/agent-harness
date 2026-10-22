#!/usr/bin/env python3
"""Temporary, local-only, fail-closed synthetic custodian signing ledger.

One private SQLite transaction binds one custodian-signed checkpoint and
an already verified witness receipt. On restart, every record in the
complete journal is re-verified. A separate readback path supports a
lost reply without repeating a signing operation. Revocation is
terminal and durable *locally*. A shared operator can still restore
the old SQLite DB AND trust head: NO independent custody or production
rollback resistance.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from observer_checkpoint import ZERO, _json_bytes, public_fingerprint
from v2_custody_checkpoint_contract import (
    UnsafeCustodyCheckpoint, checkpoint_digest,
    sign_research_checkpoint, verify_research_checkpoint,
)
from v2_custody_signer_fixture import UnsafeResearchCustodian, _root_ok, _pub64

APP_ID = 0x4F47434C  # OGCL; distinct from witness and observer SQLite
VERSION = 1
MAX_DB_SIZE = 1024 * 1024


def _canonical(x: object) -> str:
    data = _json_bytes(x)
    if len(data) > 8192:
        raise UnsafeResearchCustodian("oversized signing record")
    return data.decode("ascii")


def _read_json(s: str) -> dict:
    if not isinstance(s, str) or len(s) > 8192:
        raise UnsafeResearchCustodian("oversized signing journal artifact")
    def unique(items):
        d = {}
        for k, v in items:
            if k in d:
                raise UnsafeResearchCustodian("duplicate journal JSON field")
            d[k] = v
        return d
    try:
        x = json.loads(s, object_pairs_hook=unique)
        if not isinstance(x, dict) or _canonical(x) != s:
            raise UnsafeResearchCustodian("noncanonical journal artifact")
        return x
    except (ValueError, TypeError, UnicodeError) as exc:
        raise UnsafeResearchCustodian("invalid journal JSON") from exc


def _public_key(raw_b64: str) -> Ed25519PublicKey:
    return _pub64(raw_b64)


def _request_fields(request: object) -> None:
    fields = {
        "witness_receipt", "witness_public_base64",
        "witness_pin", "observer_public_base64",
        "worker_fingerprint", "observer_id", "worker_id",
        "witness_head", "observer_head", "scope", "generation",
        "previous_checkpoint_sha256", "issued_at", "expires_at",
    }
    if not isinstance(request, dict) or set(request) != fields:
        raise UnsafeResearchCustodian("unsupported scoped signing request")
    for name in ("witness_head", "observer_head"):
        x = request[name]
        if (not isinstance(x, list) or len(x) != 2
                or type(x[0]) is not int or not isinstance(x[1], str)):
            raise UnsafeResearchCustodian("invalid signer request head")


class SyntheticV2CustodyLedger:
    """Bounded signing history. No user task or model execution interface."""

    def __init__(self, root: Path, signer: Ed25519PrivateKey):
        self.root = Path(root)
        self.path = self.root / "custody-checkpoints.sqlite3"
        self.signer = signer
        self.custodian_pin = public_fingerprint(signer.public_key())
        _root_ok(self.root)

    def _safe_file(self, *, missing_ok=False):
        try:
            st = self.path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return False
            raise UnsafeResearchCustodian("missing signing ledger is HOLD")
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or st.st_uid != os.getuid()
                or st.st_mode & 0o077
                or st.st_size > MAX_DB_SIZE):
            raise UnsafeResearchCustodian("unsafe signing ledger inode")
        return True

    def initialize(self):
        if self._safe_file(missing_ok=True):
            raise UnsafeResearchCustodian("cannot recreate existing ledger")
        fd = os.open(
            self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
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
                db.execute("""CREATE TABLE roots (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    witness_public TEXT NOT NULL,
                    observer_public TEXT NOT NULL,
                    witness_pin TEXT NOT NULL,
                    worker_pin TEXT NOT NULL,
                    observer_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    custodian_pin TEXT NOT NULL
                )""")
                db.execute("""CREATE TABLE history (
                    generation INTEGER PRIMARY KEY,
                    request TEXT NOT NULL,
                    checkpoint TEXT NOT NULL,
                    checkpoint_sha256 TEXT NOT NULL
                )""")
                db.execute("""CREATE TABLE revoked (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    terminal_generation INTEGER NOT NULL,
                    terminal_sha256 TEXT NOT NULL
                )""")
                db.execute("COMMIT")
            finally:
                db.close()
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except BaseException:
            self.path.unlink(missing_ok=True)
            raise

    def _connect(self):
        _root_ok(self.root)
        self._safe_file()
        uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
        db = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=4)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=4000")
            if self.path.stat().st_size > MAX_DB_SIZE:
                raise UnsafeResearchCustodian("oversized ledger")
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise UnsafeResearchCustodian("corrupt ledger")
            if (db.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                    or db.execute("PRAGMA user_version").fetchone()[0] != VERSION):
                raise UnsafeResearchCustodian("wrong ledger schema")
            return db
        except BaseException:
            db.close()
            raise

    @staticmethod
    def _rows(db, table, cols):
        return db.execute("SELECT " + cols + " FROM " + table).fetchall()

    def _validate(self, db):
        roots_rows = self._rows(db, "roots",
                                "witness_public,observer_public,witness_pin,"
                                "worker_pin,observer_id,worker_id,scope,custodian_pin")
        if len(roots_rows) > 1:
            raise UnsafeResearchCustodian("duplicate signing trust root")
        roots = roots_rows[0] if roots_rows else None
        if roots and roots[-1] != self.custodian_pin:
            raise UnsafeResearchCustodian("signing key replaced or restored")
        rows = self._rows(db, "history",
                          "generation,request,checkpoint,checkpoint_sha256")
        if len(rows) > 4 or (rows and roots is None):
            raise UnsafeResearchCustodian("orphaned/oversized signing history")
        last = None
        prev_sha = ZERO
        last_witness_count = 0
        if roots:
            w_pub = _public_key(roots[0])
            o_pub = _public_key(roots[1])
            if public_fingerprint(w_pub) != roots[2]:
                raise UnsafeResearchCustodian("witness public pin mismatch")
        for ordinal, (generation, request_json, signed_json, sha) in enumerate(rows, 1):
            if type(generation) is not int or generation != ordinal:
                raise UnsafeResearchCustodian("signing sequence gap or fork")
            request = _read_json(request_json)
            signed = _read_json(signed_json)
            _request_fields(request)
            if (request["generation"] != ordinal
                    or request["previous_checkpoint_sha256"] != prev_sha
                    or request["witness_public_base64"] != roots[0]
                    or request["observer_public_base64"] != roots[1]
                    or (request["witness_pin"], request["worker_fingerprint"],
                        request["observer_id"], request["worker_id"], request["scope"])
                        != roots[2:7]
                    or checkpoint_digest(signed) != sha
                    or not verify_research_checkpoint(
                        signed,
                        trusted_custodian_key=self.signer.public_key(),
                        expected_custodian_pin=self.custodian_pin,
                        witness_receipt=request["witness_receipt"],
                        trusted_witness_key=w_pub,
                        expected_witness_pin=roots[2],
                        observer_public_key=o_pub,
                        worker_fingerprint=roots[3],
                        observer_id=roots[4],
                        worker_id=roots[5],
                        expected_witness_head=tuple(request["witness_head"]),
                        expected_observer_head=tuple(request["observer_head"]),
                        expected_scope=roots[6],
                        expected_generation=ordinal,
                        expected_previous_checkpoint_sha256=prev_sha,
                        expected_checkpoint_sha256=sha,
                        trusted_now=signed["payload"]["issued_at"],
                        minimum_witness_count=last_witness_count)):
                raise UnsafeResearchCustodian("stored signing chain invalid")
            last_witness_count = request["witness_head"][0]
            prev_sha = sha
            last = (ordinal, signed, sha, request)
        tombstones = self._rows(db, "revoked",
                                "terminal_generation,terminal_sha256")
        if len(tombstones) > 1:
            raise UnsafeResearchCustodian("duplicate local revocations")
        if tombstones:
            if (not last or tombstones[0] != (last[0], last[2])):
                raise UnsafeResearchCustodian("revocation/last head mismatch")
        return roots, last, bool(tombstones)

    def sign_once(self, request: dict) -> dict:
        _request_fields(request)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            roots, last, revoked = self._validate(db)
            if revoked:
                raise UnsafeResearchCustodian("custodian locally revoked")
            if last is not None:
                expected = (last[0] + 1, last[2])
                if (request["generation"], request["previous_checkpoint_sha256"]) != expected:
                    raise UnsafeResearchCustodian("stale/duplicate/forked signing attempt")
                if request["witness_head"][0] <= last[3]["witness_head"][0]:
                    raise UnsafeResearchCustodian("witness head did not advance")
                if request["observer_head"][0] <= last[3]["observer_head"][0]:
                    raise UnsafeResearchCustodian("observer revision did not advance")
                if last[3]["expires_at"] > request["expires_at"]:
                    raise UnsafeResearchCustodian("checkpoint expiry regressed")
            elif (request["generation"] != 1
                  or request["previous_checkpoint_sha256"] != ZERO):
                raise UnsafeResearchCustodian("genesis missing or forged")
            w_pub = _public_key(request["witness_public_base64"])
            o_pub = _public_key(request["observer_public_base64"])
            witness_pin = public_fingerprint(w_pub)
            if witness_pin != request["witness_pin"]:
                raise UnsafeResearchCustodian("claimed witness pin mismatched")
            candidate_roots = (
                request["witness_public_base64"],
                request["observer_public_base64"],
                request["witness_pin"], request["worker_fingerprint"],
                request["observer_id"], request["worker_id"],
                request["scope"], self.custodian_pin,
            )
            if roots is not None and roots != candidate_roots:
                raise UnsafeResearchCustodian("scoped trust root swap")
            checkpoint = sign_research_checkpoint(
                custodian_signer=self.signer,
                witness_receipt=request["witness_receipt"],
                trusted_witness_key=w_pub,
                expected_witness_pin=request["witness_pin"],
                observer_public_key=o_pub,
                worker_fingerprint=request["worker_fingerprint"],
                observer_id=request["observer_id"], worker_id=request["worker_id"],
                expected_witness_head=tuple(request["witness_head"]),
                expected_observer_head=tuple(request["observer_head"]),
                scope=request["scope"], generation=request["generation"],
                previous_checkpoint_sha256=request["previous_checkpoint_sha256"],
                issued_at=request["issued_at"], expires_at=request["expires_at"])
            sha = checkpoint_digest(checkpoint)
            if roots is None:
                db.execute(
                    "INSERT INTO roots VALUES (1,?,?,?,?,?,?,?,?)",
                    candidate_roots)
            db.execute(
                "INSERT INTO history VALUES (?,?,?,?)",
                (request["generation"], _canonical(request),
                 _canonical(checkpoint), sha))
            db.execute("COMMIT")
            return {
                "schema": 1, "mode": "synthetic_v2_custodian_signed_checkpoint",
                "checkpoint": checkpoint, "checkpoint_sha256": sha,
                "ledger_generation": request["generation"],
                "ledger_head_sha256": sha,
                "decision": "HOLD", "agent_dispatch_authorized": False,
                "provider_requests_forwarded": 0,
            }
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def readback(self, *, generation: int, witness_head: tuple[int, str],
                 observer_head: tuple[int, str], scope: str) -> dict:
        db = self._connect()
        try:
            db.execute("BEGIN")
            roots, last, revoked = self._validate(db)
            if revoked or not last:
                raise UnsafeResearchCustodian("revoked or empty ledger")
            if (type(generation) is not int or generation != last[0]
                    or type(witness_head) is not tuple or len(witness_head) != 2
                    or type(observer_head) is not tuple or len(observer_head) != 2
                    or witness_head != tuple(last[3]["witness_head"])
                    or observer_head != tuple(last[3]["observer_head"])
                    or scope != roots[6]):
                raise UnsafeResearchCustodian("stale/forged readback scope")
            db.execute("COMMIT")
            return {
                "schema": 1, "mode": "synthetic_v2_custodian_read_only_recovery",
                "checkpoint": last[1], "checkpoint_sha256": last[2],
                "ledger_generation": last[0], "ledger_head_sha256": last[2],
                "decision": "HOLD", "agent_dispatch_authorized": False,
                "provider_requests_forwarded": 0,
            }
        finally:
            db.close()

    def revoke(self, *, generation: int, checkpoint_sha256: str) -> dict:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            _, last, revoked = self._validate(db)
            if (revoked or last is None
                    or type(generation) is not int
                    or (generation, checkpoint_sha256) != (last[0], last[2])):
                raise UnsafeResearchCustodian("stale or duplicate revocation")
            db.execute("INSERT INTO revoked VALUES (1,?,?)",
                       (generation, checkpoint_sha256))
            db.execute("COMMIT")
            return {
                "schema": 1, "mode": "synthetic_v2_custodian_local_revocation",
                "locally_revoked": True,
                "agent_dispatch_authorized": False,
                "provider_requests_forwarded": 0, "decision": "HOLD",
            }
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
