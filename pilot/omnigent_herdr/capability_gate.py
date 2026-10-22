#!/usr/bin/env python3
"""Research-only broker lease gate: synthetic receipts, never model dispatch.

HMAC protects an ephemeral proof lease against editing. In-memory sequence,
quota, revocation, and epoch checks are not rollback-resistant custody.
Neither token acceptance nor any success result authorizes live inference.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time

DOMAIN = b"herdr-omnigent-research-synthetic-lease-v1\0"
LABEL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,95}$")
HEXDIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_TTL_SECONDS = 60
MAX_RECEIPTS = 2
MAX_TOKEN_CHARS = 2048


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url")
    decoded = base64.b64decode(value + "=" * ((-len(value)) % 4),
                               altchars=b"-_", validate=True)
    # Reject alternate encodings of the same signature bytes.
    if _encode(decoded) != value:
        raise ValueError("noncanonical base64url")
    return decoded


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _plain_label(value: object) -> bool:
    return isinstance(value, str) and bool(LABEL_RE.fullmatch(value))


def _no_duplicate_keys(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate JSON keys")
        result[k] = v
    return result


class SyntheticLeaseGate:
    """One broker-owned process and one local, non-persistent authority.

    No network, shells, model calls, or user repository reads. Peer UID is
    only a coarse OS check: same-UID isolation is NOT independent custody.
    """

    def __init__(self, secret: bytes, *, now=time.time, epoch: int = 1):
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("ephemeral signing secret must be at least 32 bytes")
        if type(epoch) is not int or epoch < 1:
            raise ValueError("invalid epoch")
        self._secret = secret
        self._now = now
        self._epoch = epoch
        self._revoked: set[str] = set()
        self._state: dict[str, tuple[int, int]] = {}
        self._last_time: int | None = None
        self._lock = threading.Lock()

    def mint(self, *, uid: int, owner: str, session: str,
             worktree_sha256: str, model_pin: str, ttl_seconds: int = 20,
             max_receipts: int = MAX_RECEIPTS) -> str:
        if (type(uid) is not int or uid < 0
                or any(not _plain_label(x) for x in (owner, session, model_pin))
                or not isinstance(worktree_sha256, str)
                or not HEXDIGEST_RE.fullmatch(worktree_sha256)
                or type(ttl_seconds) is not int
                or not (1 <= ttl_seconds <= MAX_TTL_SECONDS)
                or type(max_receipts) is not int
                or not (1 <= max_receipts <= MAX_RECEIPTS)):
            raise ValueError("invalid research lease scope")
        with self._lock:
            issued = int(self._now())
            if self._last_time is not None and issued < self._last_time:
                raise ValueError("clock rollback observed within process")
            self._last_time = issued
            payload = {
                "schema": 1, "kind": "synthetic_probe_only",
                "epoch": self._epoch, "jti": secrets.token_hex(16),
                "uid": uid, "owner": owner, "session": session,
                "worktree_sha256": worktree_sha256, "model_pin": model_pin,
                "issued_at": issued, "expires_at": issued + ttl_seconds,
                "max_receipts": max_receipts,
            }
            encoded = _encode(_canonical(payload))
            signature = _encode(hmac.digest(self._secret,
                                            DOMAIN + encoded.encode("ascii"),
                                            "sha256"))
            token = encoded + "." + signature
            if len(token) > MAX_TOKEN_CHARS:
                raise ValueError("overlong token")
            return token

    def revoke(self, token: str) -> None:
        """Revoke a known lease; never grants execution."""
        with self._lock:
            claims = self._verified_claims(token)
            if claims is not None:
                self._revoked.add(claims["jti"])

    def rotate_epoch(self) -> None:
        """Invalidate all prior ephemeral leases. Not restart-durable."""
        with self._lock:
            self._epoch += 1
            self._state.clear()
            self._revoked.clear()

    def _verified_claims(self, token: object) -> dict | None:
        if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS:
            return None
        try:
            encoded, signature = token.split(".")
            decoded_sig = _decode(signature)
            expect_sig = hmac.digest(self._secret,
                                     DOMAIN + encoded.encode("ascii"), "sha256")
            if not hmac.compare_digest(expect_sig, decoded_sig):
                return None
            claims = json.loads(_decode(encoded), object_pairs_hook=_no_duplicate_keys)
        except (ValueError, TypeError, UnicodeError):
            return None
        if not isinstance(claims, dict):
            return None
        return claims

    def check(self, request: object, *, peer_uid: int, expected_owner: str,
              expected_session: str, expected_worktree_sha256: str,
              expected_model_pin: str) -> dict:
        """Accept a bounded *synthetic* receipt, deny every other operation."""
        denied = {"status": "denied", "dispatch_authorized": False,
                  "provider_requests_forwarded": 0}
        if not isinstance(request, dict) or set(request) != {
            "schema", "op", "token", "seq",
        }:
            return denied
        if (type(request["schema"]) is not int or request["schema"] != 1
                or request["op"] != "synthetic_receipt"
                or type(request["seq"]) is not int
                or request["seq"] < 1
                or type(peer_uid) is not int):
            return denied
        with self._lock:
            claims = self._verified_claims(request["token"])
            if claims is None or set(claims) != {
                "schema", "kind", "epoch", "jti", "uid", "owner",
                "session", "worktree_sha256", "model_pin",
                "issued_at", "expires_at", "max_receipts",
            }:
                return denied
            if (claims["schema"] != 1 or type(claims["schema"]) is not int
                    or claims["kind"] != "synthetic_probe_only"
                    or type(claims["epoch"]) is not int
                    or claims["epoch"] != self._epoch
                    or not isinstance(claims["jti"], str)
                    or not re.fullmatch(r"[0-9a-f]{32}", claims["jti"])
                    or claims["jti"] in self._revoked
                    or type(claims["uid"]) is not int
                    or claims["uid"] != peer_uid
                    or claims["owner"] != expected_owner
                    or claims["session"] != expected_session
                    or claims["worktree_sha256"] != expected_worktree_sha256
                    or claims["model_pin"] != expected_model_pin
                    or any(type(claims[k]) is not int for k in (
                        "issued_at", "expires_at", "max_receipts"))
                    or not 1 <= claims["max_receipts"] <= MAX_RECEIPTS
                    or not 1 <= claims["expires_at"] - claims["issued_at"] <= MAX_TTL_SECONDS):
                return denied
            now = int(self._now())
            if (self._last_time is not None and now < self._last_time
                    or now < claims["issued_at"] or now >= claims["expires_at"]):
                return denied
            self._last_time = now
            previous_seq, used = self._state.get(claims["jti"], (0, 0))
            if request["seq"] <= previous_seq or used >= claims["max_receipts"]:
                return denied
            self._state[claims["jti"]] = (request["seq"], used + 1)
            return {
                "status": "synthetic_receipt_only",
                "dispatch_authorized": False,
                "provider_requests_forwarded": 0,
                "receipt_number": used + 1,
            }


if __name__ == "__main__":
    raise SystemExit("Research-only module: not a runnable agent server")
