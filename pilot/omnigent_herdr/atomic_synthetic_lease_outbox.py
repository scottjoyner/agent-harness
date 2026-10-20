#!/usr/bin/env python3
"""Opt-in V2 SYNTHETIC lease debit + authenticated outbox in ONE SQLite TX.

This closes only the *local* gap where a debit is committed without a
durable, key-bound outbox intent. It does NOT atomically publish an
Ed25519 receipt to a separate observer or establish independent custody,
external replay protection, real provider admission, model dispatch, or
machine-level trust. Existing V1 lease DBs cannot open under this V2
adapter and existing V1 calls must not silently upgrade.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from durable_lease_store import (
    DurableSyntheticLeaseGate, UnsafeLeaseState,
)

OUTBOX_DOMAIN = b"og-synthetic-atomic-outbox-intent-v1\0"
MAC_DOMAIN = b"og-synthetic-atomic-outbox-hmac-v1\0"
HEX = re.compile(r"^[a-f0-9]{64}$")
OP = re.compile(r"^[a-f0-9]{32}$")
MAX_EVENTS = 128
CORE_FIELDS = (
    "record_no", "operation_id", "lease_jti", "request_sha256",
    "before_state_sha256", "after_state_sha256", "epoch",
    "sequence", "receipt_number", "previous_record_sha256",
)


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _event_hash(event: dict) -> str:
    return hashlib.sha256(OUTBOX_DOMAIN + _canonical(event)).hexdigest()


class AtomicSyntheticLeaseOutbox(DurableSyntheticLeaseGate):
    """Dedicated schema V2 synthetic broker: fail-closed atomic debit intent.

    One signing key is passed by the test harness, not persisted. The
    outbox's HMAC can be independently checked only by a verifier already
    holding that secret; separate observer Ed25519 publication is NOT
    included in this class. No admission grant is ever returned.
    """

    def __init__(self, path: Path, secret: bytes, *,
                 now=None, create: bool = False):
        import time
        super().__init__(
            path, secret, now=time.time if now is None else now,
            create=create, _atomic_outbox=True,
        )

    @contextmanager
    def _transaction(self, *, write=True):
        with super()._transaction(write=write) as (db, meta):
            # Make every API call, including unrelated mint/revoke/epoch
            # changes, prove it has not left a committed outbox state
            # inconsistent with the corresponding lease state.
            self._validate_outbox(db, meta)
            yield db, meta
            updated = db.execute(
                "SELECT epoch,last_clock FROM metadata WHERE id=1"
            ).fetchone()
            self._validate_outbox(db, updated)

    def _mac(self, digest: str) -> str:
        return hmac.digest(
            self._secret, MAC_DOMAIN + bytes.fromhex(digest), "sha256",
        ).hex()

    def _validate_outbox(self, db, meta) -> None:
        rows = db.execute(
            "SELECT record_no,operation_id,lease_jti,request_sha256,"
            "before_state_sha256,after_state_sha256,epoch,sequence,"
            "receipt_number,previous_record_sha256,record_sha256,hmac_sha256 "
            "FROM atomic_outbox ORDER BY record_no"
        ).fetchall()
        if len(rows) > MAX_EVENTS:
            raise UnsafeLeaseState("outbox event cap exceeded")
        previous = "0" * 64
        before = None
        count_per_lease = {}
        seq_per_lease = {}
        for number, row in enumerate(rows, 1):
            core = dict(zip(CORE_FIELDS, row[:len(CORE_FIELDS)]))
            event_digest, mac = row[-2:]
            if (type(row[0]) is not int or row[0] != number
                    or not isinstance(row[1], str)
                    or not OP.fullmatch(row[1])
                    or not isinstance(row[2], str)
                    or not OP.fullmatch(row[2])
                    or any(not isinstance(field, str) or not HEX.fullmatch(field)
                           for field in (row[3], row[4], row[5], row[9],
                                         event_digest, mac))
                    or row[9] != previous
                    or type(row[6]) is not int or row[6] < 1
                    or type(row[7]) is not int or row[7] < 1
                    or type(row[8]) is not int
                    or row[8] not in (1, 2)
                    or (before is not None and row[4] != before)):
                raise UnsafeLeaseState("invalid outbox order or scope")
            calculated = _event_hash(core)
            if (not hmac.compare_digest(calculated, event_digest)
                    or not hmac.compare_digest(self._mac(calculated), mac)):
                raise UnsafeLeaseState("missing or forged outbox HMAC")
            jti = row[2]
            used = count_per_lease.get(jti, 0) + 1
            if (row[8] != used
                    or row[7] <= seq_per_lease.get(jti, 0)):
                raise UnsafeLeaseState("replayed or nonmonotonic lease record")
            count_per_lease[jti] = used
            seq_per_lease[jti] = row[7]
            previous = calculated
            before = row[5]
        leases = db.execute(
            "SELECT jti,epoch,last_seq,used,max_receipts FROM leases"
        ).fetchall()
        expected_epoch = meta[0]
        for jti, epoch, seq, used, maximum in leases:
            if (epoch != expected_epoch or type(used) is not int
                    or used != count_per_lease.get(jti, 0)
                    or seq != seq_per_lease.get(jti, 0)
                    or used > maximum):
                raise UnsafeLeaseState("lease debit missing matching outbox")
        if len(count_per_lease) != sum(1 for _, _, _, used, _ in leases if used):
            raise UnsafeLeaseState("orphaned outbox lease identifier")
        if rows and (
            rows[-1][5] != self._state_digest_db(db, meta)
        ):
            raise UnsafeLeaseState("lease DB differs from latest outbox intent")

    def _commit_outbox(self, db, *, operation_id, request, claims,
                       before_state_sha256, after_state_sha256,
                       epoch, sequence, receipt_number) -> dict:
        rows = db.execute(
            "SELECT record_no,record_sha256 FROM atomic_outbox "
            "ORDER BY record_no DESC LIMIT 1"
        ).fetchone()
        next_no = rows[0] + 1 if rows else 1
        if next_no > MAX_EVENTS:
            raise UnsafeLeaseState("atomic outbox full")
        fields = {
            "record_no": next_no,
            "operation_id": operation_id,
            "lease_jti": claims["jti"],
            "request_sha256": hashlib.sha256(_canonical(request)).hexdigest(),
            "before_state_sha256": before_state_sha256,
            "after_state_sha256": after_state_sha256,
            "epoch": epoch,
            "sequence": sequence,
            "receipt_number": receipt_number,
            "previous_record_sha256": rows[1] if rows else "0" * 64,
        }
        record_sha = _event_hash(fields)
        mac = self._mac(record_sha)
        db.execute(
            "INSERT INTO atomic_outbox VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(fields[k] for k in CORE_FIELDS) + (record_sha, mac))
        return {**fields, "record_sha256": record_sha, "hmac_sha256": mac}

    def export_head_event(self, *, trusted_anchor: tuple[int, str]) -> dict:
        """Read back exactly the latest HMAC-verified V2 synthetic debit.

        Caller-supplied head is REQUIRED and must equal the validated DB
        head.  The HMAC key, token, full request and arbitrary DB bytes
        are never exposed to the observer.  This is worker SELF-report,
        not separately attested resource authority.
        """
        with self._transaction(write=False) as (db, meta):
            row = db.execute(
                "SELECT " + ",".join(CORE_FIELDS)
                + ",record_sha256,hmac_sha256 FROM atomic_outbox "
                "ORDER BY record_no DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise UnsafeLeaseState("no committed synthetic outbox event")
            fields = dict(zip(
                CORE_FIELDS + ("record_sha256", "hmac_sha256"), row
            ))
            actual = (fields["record_no"], fields["record_sha256"])
            if (not isinstance(trusted_anchor, tuple)
                    or len(trusted_anchor) != 2
                    or type(trusted_anchor[0]) is not int
                    or trusted_anchor != actual):
                raise UnsafeLeaseState("missing or stale trusted outbox head")
            return {
                "schema": 1,
                "kind": "synthetic_v2_outbox_head",
                "outbox_event": {k: fields[k] for k in CORE_FIELDS},
                "outbox_record_sha256": fields["record_sha256"],
                "lease_state_sha256": self._state_digest_db(db, meta),
                "agent_dispatch_authorized": False,
                "provider_requests_forwarded": 0,
            }

    def inspect(self, *, trusted_anchor: tuple[int, str] | None) -> dict:
        """Non-authorizing outbox read, pinned to a caller-controlled head."""
        with self._transaction(write=False) as (db, meta):
            last = db.execute(
                "SELECT record_no,record_sha256 FROM atomic_outbox "
                "ORDER BY record_no DESC LIMIT 1"
            ).fetchone()
            actual = last if last is not None else (0, "0" * 64)
            if (not isinstance(trusted_anchor, tuple)
                    or len(trusted_anchor) != 2
                    or type(trusted_anchor[0]) is not int
                    or trusted_anchor != actual):
                raise UnsafeLeaseState("untrusted, stale or rolled-back head")
            return {
                "schema": 2,
                "mode": "synthetic_atomic_lease_outbox",
                "event_count": actual[0],
                "head_sha256": actual[1],
                "lease_state_sha256": self._state_digest_db(db, meta),
                "local_debit_intent_atomic": True,
                "outbox_published_to_observer": False,
                "independent_anchor_custody_verified": False,
                "rollback_resistance_verified": False,
                "source_attestation_verified": False,
                "agent_dispatch_authorized": False,
                "model_egress_authorized": False,
                "provider_requests_forwarded": 0,
                "decision": "HOLD",
            }


if __name__ == "__main__":
    raise SystemExit("Synthetic atomic outbox only; no live agent admission")
