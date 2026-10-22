#!/usr/bin/env python3
"""Strict Ed25519 protocol for publication of one SYNTHETIC V2 outbox head.

Neither an observer's receipt nor this signature establishes independent
worker custody or production transaction authenticity. The caller must
pin the worker/observer keys OUT OF BAND; test fixtures deliberately
bootstrap keys over an existing SSH session and never assert independence.

No agent/model dispatch, provider tokens, publication service, or
automatic retry/refund API is implemented.
"""
from __future__ import annotations

import hashlib
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from capability_gate import _decode, _encode
from observer_checkpoint import _json_bytes, public_fingerprint
from atomic_synthetic_lease_outbox import CORE_FIELDS, _event_hash

WORKER_DOMAIN = b"herdr-synthetic-v2-outbox-publish-v1\0"
ACK_DOMAIN = b"herdr-synthetic-v2-observer-ack-v1\0"
DELIVERY_DOMAIN = b"herdr-synthetic-v2-ack-delivery-v1\0"
HEX64 = re.compile(r"^[a-f0-9]{64}$")
ROLE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$")
OP = re.compile(r"^[a-f0-9]{32}$")
PUB = {
    "schema", "kind", "worker_id", "observer_id",
    "worker_fingerprint", "observer_fingerprint",
    "outbox_event", "outbox_record_sha256", "lease_state_sha256",
    "agent_dispatch_authorized", "provider_requests_forwarded",
}
ACK = {
    "schema", "kind", "worker_id", "observer_id",
    "worker_fingerprint", "observer_fingerprint", "publication_sha256",
    "operation_id", "outbox_record_no", "outbox_record_sha256",
    "lease_state_sha256", "observer_generation",
    "previous_checkpoint_sha256", "agent_dispatch_authorized",
    "provider_requests_forwarded",
}


DELIVERY = {
    "schema", "kind", "worker_id", "observer_id",
    "worker_fingerprint", "observer_fingerprint",
    "publication_sha256", "observer_ack_sha256",
    "operation_id", "outbox_record_no", "outbox_record_sha256",
    "lease_state_sha256", "agent_dispatch_authorized",
    "provider_requests_forwarded",
}


def _hex(value: object) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _pub_payload(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != PUB:
        return False
    if (type(value["schema"]) is not int or value["schema"] != 1
            or value["kind"] != "synthetic_v2_outbox_publication"
            or not all(isinstance(value[k], str) and ROLE.fullmatch(value[k])
                       for k in ("worker_id", "observer_id"))
            or value["worker_id"] == value["observer_id"]
            or not _hex(value["worker_fingerprint"])
            or not _hex(value["observer_fingerprint"])
            or value["worker_fingerprint"] == value["observer_fingerprint"]
            or not _hex(value["outbox_record_sha256"])
            or not _hex(value["lease_state_sha256"])
            or value["agent_dispatch_authorized"] is not False
            or type(value["provider_requests_forwarded"]) is not int
            or value["provider_requests_forwarded"] != 0):
        return False
    e = value["outbox_event"]
    if not isinstance(e, dict) or set(e) != set(CORE_FIELDS):
        return False
    if (type(e["record_no"]) is not int or e["record_no"] < 1
            or not isinstance(e["operation_id"], str)
            or not OP.fullmatch(e["operation_id"])
            or not isinstance(e["lease_jti"], str)
            or not OP.fullmatch(e["lease_jti"])
            or type(e["epoch"]) is not int or e["epoch"] < 1
            or type(e["sequence"]) is not int or e["sequence"] < 1
            or type(e["receipt_number"]) is not int
            or not 1 <= e["receipt_number"] <= 2
            or not all(_hex(e[k]) for k in (
                "request_sha256", "before_state_sha256",
                "after_state_sha256", "previous_record_sha256"))
            or e["before_state_sha256"] == e["after_state_sha256"]
            or value["outbox_record_sha256"] != _event_hash(e)
            or value["lease_state_sha256"] != e["after_state_sha256"]):
        return False
    return True


def _ack_payload(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != ACK:
        return False
    return (
        type(value["schema"]) is int and value["schema"] == 1
        and value["kind"] == "synthetic_v2_observer_ack"
        and all(isinstance(value[k], str) and ROLE.fullmatch(value[k])
                for k in ("worker_id", "observer_id"))
        and value["worker_id"] != value["observer_id"]
        and all(_hex(value[k]) for k in (
            "worker_fingerprint", "observer_fingerprint",
            "publication_sha256", "outbox_record_sha256",
            "lease_state_sha256", "previous_checkpoint_sha256"))
        and value["worker_fingerprint"] != value["observer_fingerprint"]
        and isinstance(value["operation_id"], str)
        and OP.fullmatch(value["operation_id"]) is not None
        and type(value["outbox_record_no"]) is int
        and value["outbox_record_no"] >= 1
        and type(value["observer_generation"]) is int
        and value["observer_generation"] >= 1
        and value["agent_dispatch_authorized"] is False
        and type(value["provider_requests_forwarded"]) is int
        and value["provider_requests_forwarded"] == 0
    )


def _delivery_payload(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != DELIVERY:
        return False
    return (
        type(value["schema"]) is int and value["schema"] == 1
        and value["kind"] == "synthetic_v2_worker_ack_delivery"
        and all(isinstance(value[k], str) and ROLE.fullmatch(value[k])
                for k in ("worker_id", "observer_id"))
        and value["worker_id"] != value["observer_id"]
        and all(_hex(value[k]) for k in (
            "worker_fingerprint", "observer_fingerprint",
            "publication_sha256", "observer_ack_sha256",
            "outbox_record_sha256", "lease_state_sha256"))
        and value["worker_fingerprint"] != value["observer_fingerprint"]
        and isinstance(value["operation_id"], str)
        and OP.fullmatch(value["operation_id"]) is not None
        and type(value["outbox_record_no"]) is int
        and value["outbox_record_no"] >= 1
        and value["agent_dispatch_authorized"] is False
        and type(value["provider_requests_forwarded"]) is int
        and value["provider_requests_forwarded"] == 0
    )


def _sign(key: Ed25519PrivateKey, *, payload: dict, domain: bytes) -> dict:
    return {
        "algorithm": "Ed25519", "payload": payload,
        "signature": _encode(key.sign(domain + _json_bytes(payload))),
    }


def _verify(envelope: object, *, key: Ed25519PublicKey,
            pin: str, domain: bytes, validate, expected: dict) -> bool:
    if (not isinstance(envelope, dict)
            or set(envelope) != {"algorithm", "payload", "signature"}
            or envelope.get("algorithm") != "Ed25519"
            or not validate(envelope.get("payload"))
            or not _hex(pin) or public_fingerprint(key) != pin
            or not isinstance(expected, dict)
            or set(expected) != set(envelope["payload"])):
        return False
    payload = envelope["payload"]
    if not all(type(payload[k]) is type(expected[k])
               and payload[k] == expected[k] for k in expected):
        return False
    try:
        key.verify(
            _decode(envelope["signature"]), domain + _json_bytes(payload))
        return True
    except (InvalidSignature, ValueError, TypeError, UnicodeError):
        return False


def publication_sha256(envelope: dict) -> str:
    return hashlib.sha256(_json_bytes(envelope)).hexdigest()


def worker_publish(*, signer: Ed25519PrivateKey, worker_id: str,
                   observer_id: str, observer_fingerprint: str,
                   exported_head: dict) -> dict:
    if (not isinstance(exported_head, dict)
            or set(exported_head) != {
                "schema", "kind", "outbox_event", "outbox_record_sha256",
                "lease_state_sha256", "agent_dispatch_authorized",
                "provider_requests_forwarded",
            }
            or exported_head["schema"] != 1
            or exported_head["kind"] != "synthetic_v2_outbox_head"):
        raise ValueError("not a validated V2 synthetic head export")
    payload = {
        "schema": 1, "kind": "synthetic_v2_outbox_publication",
        "worker_id": worker_id, "observer_id": observer_id,
        "worker_fingerprint": public_fingerprint(signer.public_key()),
        "observer_fingerprint": observer_fingerprint,
        **{k: exported_head[k] for k in (
            "outbox_event", "outbox_record_sha256",
            "lease_state_sha256", "agent_dispatch_authorized",
            "provider_requests_forwarded")},
    }
    if not _pub_payload(payload):
        raise ValueError("invalid V2 outbox publication scope")
    return _sign(signer, payload=payload, domain=WORKER_DOMAIN)


def observer_verify_publication(*, envelope: object,
                                worker_public_key: Ed25519PublicKey,
                                trusted_worker_fingerprint: str,
                                trusted_observer_fingerprint: str,
                                expected_worker_id: str,
                                expected_observer_id: str,
                                expected_operation_id: str,
                                expected_outbox_head: tuple[int, str],
                                expected_before_state: str,
                                expected_after_state: str) -> bool:
    if (not isinstance(expected_outbox_head, tuple)
            or len(expected_outbox_head) != 2
            or type(expected_outbox_head[0]) is not int):
        return False
    if not isinstance(envelope, dict) or not _pub_payload(
            envelope.get("payload")):
        return False
    p = envelope["payload"]
    expected = {
        **p, "worker_id": expected_worker_id,
        "observer_id": expected_observer_id,
        "worker_fingerprint": trusted_worker_fingerprint,
        "observer_fingerprint": trusted_observer_fingerprint,
        "outbox_record_sha256": expected_outbox_head[1],
        "lease_state_sha256": expected_after_state,
        "outbox_event": {
            **p["outbox_event"],
            "record_no": expected_outbox_head[0],
            "operation_id": expected_operation_id,
            "before_state_sha256": expected_before_state,
            "after_state_sha256": expected_after_state,
        },
    }
    # The source of 'other' event fields is the signed worker claim.
    # No claim of independently checking a real DB transaction.
    return _verify(
        envelope, key=worker_public_key, pin=trusted_worker_fingerprint,
        domain=WORKER_DOMAIN, validate=_pub_payload, expected=expected)


def observer_sign_ack(*, signer: Ed25519PrivateKey, publication: dict,
                      worker_public_key: Ed25519PublicKey,
                      trusted_worker_fingerprint: str,
                      expected_worker_id: str, expected_observer_id: str,
                      expected_operation_id: str,
                      expected_outbox_head: tuple[int, str],
                      expected_before_state: str,
                      expected_after_state: str,
                      observer_generation: int,
                      previous_checkpoint_sha256: str) -> dict:
    # Never sign a caller-supplied unsigned/self-described publication.
    # Recheck the complete worker signature and the expected operation
    # before creating any observer ACK bytes.
    if not observer_verify_publication(
            envelope=publication,
            worker_public_key=worker_public_key,
            trusted_worker_fingerprint=trusted_worker_fingerprint,
            trusted_observer_fingerprint=public_fingerprint(signer.public_key()),
            expected_worker_id=expected_worker_id,
            expected_observer_id=expected_observer_id,
            expected_operation_id=expected_operation_id,
            expected_outbox_head=expected_outbox_head,
            expected_before_state=expected_before_state,
            expected_after_state=expected_after_state):
        raise ValueError("unverified worker publication refused")
    pub = publication["payload"]
    e = pub["outbox_event"]
    payload = {
        "schema": 1, "kind": "synthetic_v2_observer_ack",
        "worker_id": pub["worker_id"],
        "observer_id": pub["observer_id"],
        "worker_fingerprint": pub["worker_fingerprint"],
        "observer_fingerprint": public_fingerprint(signer.public_key()),
        "publication_sha256": publication_sha256(publication),
        "operation_id": e["operation_id"],
        "outbox_record_no": e["record_no"],
        "outbox_record_sha256": pub["outbox_record_sha256"],
        "lease_state_sha256": pub["lease_state_sha256"],
        "observer_generation": observer_generation,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
    }
    if (not _ack_payload(payload)
            or payload["observer_fingerprint"] != pub["observer_fingerprint"]):
        raise ValueError("observer signer is not published trusted role")
    return _sign(signer, payload=payload, domain=ACK_DOMAIN)


def worker_verify_ack(*, envelope: object,
                      observer_public_key: Ed25519PublicKey,
                      trusted_observer_fingerprint: str,
                      publication: dict, expected_generation: int,
                      previous_checkpoint_sha256: str) -> bool:
    if not isinstance(publication, dict) or not _pub_payload(
            publication.get("payload")):
        return False
    pub = publication["payload"]
    if pub["observer_fingerprint"] != trusted_observer_fingerprint:
        return False
    e = pub["outbox_event"]
    expected = {
        "schema": 1, "kind": "synthetic_v2_observer_ack",
        "worker_id": pub["worker_id"],
        "observer_id": pub["observer_id"],
        "worker_fingerprint": pub["worker_fingerprint"],
        "observer_fingerprint": trusted_observer_fingerprint,
        "publication_sha256": publication_sha256(publication),
        "operation_id": e["operation_id"],
        "outbox_record_no": e["record_no"],
        "outbox_record_sha256": pub["outbox_record_sha256"],
        "lease_state_sha256": pub["lease_state_sha256"],
        "observer_generation": expected_generation,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
    }
    return _verify(
        envelope, key=observer_public_key,
        pin=trusted_observer_fingerprint,
        domain=ACK_DOMAIN, validate=_ack_payload, expected=expected)


def worker_sign_delivery(*, signer: Ed25519PrivateKey,
                         publication: dict, observer_ack: dict,
                         observer_public_key: Ed25519PublicKey,
                         trusted_observer_fingerprint: str,
                         expected_generation: int,
                         previous_checkpoint_sha256: str) -> dict:
    if (not isinstance(publication, dict)
            or not _pub_payload(publication.get("payload"))
            or publication["payload"]["worker_fingerprint"]
            != public_fingerprint(signer.public_key())
            or not worker_verify_ack(
                envelope=observer_ack,
                observer_public_key=observer_public_key,
                trusted_observer_fingerprint=trusted_observer_fingerprint,
                publication=publication,
                expected_generation=expected_generation,
                previous_checkpoint_sha256=previous_checkpoint_sha256)):
        raise ValueError("invalid signed observer ACK; no worker delivery")
    p = publication["payload"]
    event = p["outbox_event"]
    payload = {
        "schema": 1, "kind": "synthetic_v2_worker_ack_delivery",
        "worker_id": p["worker_id"],
        "observer_id": p["observer_id"],
        "worker_fingerprint": p["worker_fingerprint"],
        "observer_fingerprint": p["observer_fingerprint"],
        "publication_sha256": publication_sha256(publication),
        "observer_ack_sha256": publication_sha256(observer_ack),
        "operation_id": event["operation_id"],
        "outbox_record_no": event["record_no"],
        "outbox_record_sha256": p["outbox_record_sha256"],
        "lease_state_sha256": p["lease_state_sha256"],
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
    }
    if not _delivery_payload(payload):
        raise ValueError("invalid worker delivery claims")
    return _sign(signer, payload=payload, domain=DELIVERY_DOMAIN)


def observer_verify_delivery(*, envelope: object,
                             worker_public_key: Ed25519PublicKey,
                             trusted_worker_fingerprint: str,
                             trusted_observer_fingerprint: str,
                             publication: dict, observer_ack: dict) -> bool:
    if (not isinstance(publication, dict)
            or not _pub_payload(publication.get("payload"))
            or not isinstance(observer_ack, dict)
            or not _ack_payload(observer_ack.get("payload"))):
        return False
    p = publication["payload"]
    if (p["worker_fingerprint"] != trusted_worker_fingerprint
            or p["observer_fingerprint"] != trusted_observer_fingerprint):
        return False
    event = p["outbox_event"]
    expected = {
        "schema": 1, "kind": "synthetic_v2_worker_ack_delivery",
        "worker_id": p["worker_id"], "observer_id": p["observer_id"],
        "worker_fingerprint": trusted_worker_fingerprint,
        "observer_fingerprint": trusted_observer_fingerprint,
        "publication_sha256": publication_sha256(publication),
        "observer_ack_sha256": publication_sha256(observer_ack),
        "operation_id": event["operation_id"],
        "outbox_record_no": event["record_no"],
        "outbox_record_sha256": p["outbox_record_sha256"],
        "lease_state_sha256": p["lease_state_sha256"],
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
    }
    return _verify(
        envelope, key=worker_public_key,
        pin=trusted_worker_fingerprint,
        domain=DELIVERY_DOMAIN, validate=_delivery_payload,
        expected=expected)


if __name__ == "__main__":
    raise SystemExit("No publication service or live model/agent dispatch")
