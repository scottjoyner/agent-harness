#!/usr/bin/env python3
"""Two-stage deny-only observer ACKs for synthetic lease receipts.

The observer and its high-water state are ephemeral fixtures. An ACK
confirms a signed *observation* of a committed worker state, but does not
establish independent custody, durable publication, or distributed
atomicity. A worker may consume a receipt and still return DENIED when
the acknowledgement is lost. Never use for provider traffic or models.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from capability_gate import _decode, _encode
from durable_lease_store import DENIED, DurableSyntheticLeaseGate
from observer_checkpoint import (
    ZERO, checkpoint_sha256, public_fingerprint, sign_fixture, _json_bytes,
)

DOMAIN = b"og-synthetic-observer-ack-v1\0"
HEX = re.compile(r"^[a-f0-9]{64}$")
OP = re.compile(r"^[a-f0-9]{32}$")
BASE_FIELDS = {
    "schema", "kind", "observer_id", "worker_id", "operation_id",
    "request_sha256", "before_state_sha256", "generation",
    "previous_checkpoint_sha256",
}
ACK_FIELDS = BASE_FIELDS | {"after_state_sha256", "checkpoint_sha256"}


def request_digest(request: dict) -> str:
    return hashlib.sha256(_json_bytes(request)).hexdigest()


def _signed(key: Ed25519PrivateKey, payload: dict) -> dict:
    return {
        "algorithm": "Ed25519",
        "payload": payload,
        "signature": _encode(key.sign(DOMAIN + _json_bytes(payload))),
    }


def _validate_signed(envelope: object, key: Ed25519PublicKey,
                     kind: str, fields: set[str]) -> bool:
    if (not isinstance(envelope, dict)
            or set(envelope) != {"algorithm", "payload", "signature"}
            or envelope["algorithm"] != "Ed25519"
            or not isinstance(envelope["payload"], dict)
            or set(envelope["payload"]) != fields
            or type(envelope["payload"].get("schema")) is not int
            or envelope["payload"]["schema"] != 1
            or envelope["payload"]["kind"] != kind):
        return False
    try:
        key.verify(_decode(envelope["signature"]),
                   DOMAIN + _json_bytes(envelope["payload"]))
    except (InvalidSignature, ValueError, TypeError, UnicodeError):
        return False
    return True


def verify_prepare(record: object, *, public_key: Ed25519PublicKey,
                   fingerprint: str, expected: dict) -> bool:
    if (public_fingerprint(public_key) != fingerprint
            or not _validate_signed(record, public_key,
                                    "synthetic_prepare", BASE_FIELDS)):
        return False
    return all(type(record["payload"].get(k)) is type(v)
               and record["payload"][k] == v for k, v in expected.items())


def verify_ack(record: object, *, public_key: Ed25519PublicKey,
               fingerprint: str, expected: dict) -> bool:
    if (public_fingerprint(public_key) != fingerprint
            or not _validate_signed(record, public_key,
                                    "synthetic_commit_ack", ACK_FIELDS)):
        return False
    return all(type(record["payload"].get(k)) is type(v)
               and record["payload"][k] == v for k, v in expected.items())


class EphemeralObserver:
    """One in-memory research-only observer. Never an admitted signer."""

    def __init__(self, *, signer: Ed25519PrivateKey, observer_id: str,
                 worker_id: str, initial_state: str):
        if not HEX.fullmatch(initial_state) or observer_id == worker_id:
            raise ValueError("bad observer fixture identity or state")
        self._signer = signer
        self.observer_id = observer_id
        self.worker_id = worker_id
        self.public_key = signer.public_key()
        self.fingerprint = public_fingerprint(self.public_key)
        self.current = sign_fixture(
            observer_key=signer, observer_id=observer_id,
            worker_id=worker_id, state_sha256=initial_state,
            generation=1, previous_checkpoint_sha256=ZERO,
            created_unix=1000)
        self.pending: dict | None = None
        self.fenced = False
        self.completed_ids: set[str] = set()
        self.last_ack: dict | None = None

    @property
    def state(self) -> str:
        return self.current["payload"]["state_sha256"]

    @property
    def generation(self) -> int:
        return self.current["payload"]["generation"]

    def prepare(self, *, operation_id: str,
                request_sha256: str, previous_state: str) -> dict:
        if (self.pending is not None or self.fenced
                or operation_id in self.completed_ids
                or not OP.fullmatch(operation_id)
                or not HEX.fullmatch(request_sha256)
                or previous_state != self.state):
            raise ValueError("observer is fenced or worker state is stale")
        payload = {
            "schema": 1, "kind": "synthetic_prepare",
            "observer_id": self.observer_id, "worker_id": self.worker_id,
            "operation_id": operation_id,
            "request_sha256": request_sha256,
            "before_state_sha256": previous_state,
            "generation": self.generation + 1,
            "previous_checkpoint_sha256": checkpoint_sha256(self.current),
        }
        self.pending = _signed(self._signer, payload)
        return self.pending

    def acknowledge(self, *, prepared: object, after_state: str,
                    independently_observed_state: str) -> dict:
        if (self.pending is None or self.pending != prepared
                or not HEX.fullmatch(after_state)
                or after_state == self.state
                or independently_observed_state != after_state):
            raise ValueError("unconfirmed post-commit state")
        prep = prepared["payload"]
        current_checkpoint_hash = checkpoint_sha256(self.current)
        if not verify_prepare(
                prepared, public_key=self.public_key,
                fingerprint=self.fingerprint,
                expected={
                    "observer_id": self.observer_id,
                    "worker_id": self.worker_id,
                    "before_state_sha256": self.state,
                    "generation": self.generation + 1,
                    "previous_checkpoint_sha256": current_checkpoint_hash,
                }):
            raise ValueError("invalid signed prepare")
        checkpoint = sign_fixture(
            observer_key=self._signer,
            observer_id=self.observer_id,
            worker_id=self.worker_id,
            state_sha256=after_state,
            generation=self.generation + 1,
            previous_checkpoint_sha256=current_checkpoint_hash,
            created_unix=1000 + self.generation,
        )
        ack = _signed(self._signer, {
            **prep, "kind": "synthetic_commit_ack",
            "after_state_sha256": after_state,
            "checkpoint_sha256": checkpoint_sha256(checkpoint),
        })
        self.current = checkpoint
        self.completed_ids.add(prep["operation_id"])
        self.last_ack = ack
        self.pending = None
        # Do not accept another transition until the worker acknowledges
        # the observer's signed receipt. A missing ACK must fence progress.
        self.fenced = True
        return ack

    def confirm_ack_delivery(self, ack: dict) -> None:
        if not self.fenced or self.last_ack != ack:
            raise ValueError("unconfirmed or replayed ACK")
        self.fenced = False

    def abort_if_unchanged(self, *, prepared: dict, observed_state: str) -> None:
        if self.pending != prepared or observed_state != self.state:
            raise ValueError("uncertain commit: observer remains fenced")
        self.pending = None


class WitnessedSyntheticReceipt:
    """Return synthetic success only after verifying a signed state ACK.

    A missing/bad ACK returns DENIED *after* SQLite may have committed.
    Until separately reconciled the two nodes must remain fenced.
    The observer is NOT independently owned in this fixture.
    """

    def __init__(self, worker: DurableSyntheticLeaseGate,
                 observer: EphemeralObserver, *,
                 trusted_public_key: Ed25519PublicKey,
                 trusted_fingerprint: str):
        self.worker = worker
        self.observer = observer
        self.trusted_public_key = trusted_public_key
        self.trusted_fingerprint = trusted_fingerprint

    def execute(self, request: dict, *, peer_uid: int,
                expected_owner: str, expected_session: str,
                expected_worktree_sha256: str, expected_model_pin: str,
                observed_postcommit: Callable[[], str] | None = None,
                deliver_ack: Callable[[dict], object] | None = None,
                operation_id: str | None = None) -> dict:
        denied = {**DENIED, "observer_acknowledged": False,
                  "agent_dispatch_authorized": False,
                  "model_egress_authorized": False}
        try:
            before = self.worker.state_digest()
            if before != self.observer.state:
                return denied
            opid = operation_id or secrets.token_hex(16)
            request_sha = request_digest(request)
            prep = self.observer.prepare(
                operation_id=opid, request_sha256=request_sha,
                previous_state=before)
            prev_cp = prep["payload"]["previous_checkpoint_sha256"]
            if not verify_prepare(
                    prep, public_key=self.trusted_public_key,
                    fingerprint=self.trusted_fingerprint,
                    expected={
                        "operation_id": opid,
                        "request_sha256": request_sha,
                        "before_state_sha256": before,
                        "generation": self.observer.generation + 1,
                        "previous_checkpoint_sha256": prev_cp,
                    }):
                return denied
            accepted = self.worker.check(
                request, peer_uid=peer_uid,
                expected_owner=expected_owner,
                expected_session=expected_session,
                expected_worktree_sha256=expected_worktree_sha256,
                expected_model_pin=expected_model_pin)
            after = self.worker.state_digest()
            if accepted.get("status") != "synthetic_receipt_only":
                if after == before:
                    self.observer.abort_if_unchanged(
                        prepared=prep, observed_state=after)
                return denied
            # No implicit self-observation: the proposed observer must
            # supply a separate read-back (still not attested here).
            if observed_postcommit is None:
                return denied
            external_observation = observed_postcommit()
            ack = self.observer.acknowledge(
                prepared=prep, after_state=after,
                independently_observed_state=external_observation)
            delivered = deliver_ack(ack) if deliver_ack else ack
            expected = {
                "operation_id": opid,
                "request_sha256": request_sha,
                "before_state_sha256": before,
                "after_state_sha256": after,
                "generation": prep["payload"]["generation"],
                "previous_checkpoint_sha256": prev_cp,
                "checkpoint_sha256": checkpoint_sha256(
                    self.observer.current),
            }
            if not verify_ack(
                    delivered, public_key=self.trusted_public_key,
                    fingerprint=self.trusted_fingerprint,
                    expected=expected):
                return denied
            self.observer.confirm_ack_delivery(ack)
            return {**accepted, "observer_acknowledged": True,
                    "agent_dispatch_authorized": False,
                    "model_egress_authorized": False,
                    "dispatch_authorized": False,
                    "provider_requests_forwarded": 0}
        except (ValueError, RuntimeError, OSError, TypeError):
            return denied


if __name__ == "__main__":
    raise SystemExit("No broker service, agent launch, or model forwarding")
