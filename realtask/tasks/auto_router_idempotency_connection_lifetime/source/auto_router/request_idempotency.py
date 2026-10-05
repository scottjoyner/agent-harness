from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi.responses import JSONResponse

from auto_router.settings import get_settings

_PROTECTED_PATHS = {
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/completions",
    "/v1/embeddings",
}
_TERMINAL = {"completed", "failed", "cancelled", "possibly_accepted"}


def _database_path(database_url: str) -> Path:
    if database_url == "sqlite:///:memory:":
        return Path(":memory:")
    parsed = urlparse(database_url)
    if parsed.scheme != "sqlite":
        raise RuntimeError("request idempotency requires the configured SQLite ledger")
    raw = parsed.path
    if raw.startswith("//"):
        raw = raw[1:]
    return Path(raw or "router.sqlite3")


def _header(scope: dict[str, Any], name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1").strip()
    return ""


def _token_jti(token: str) -> str:
    parts = token.split(".")
    if len(parts) != 3:
        return ""
    try:
        payload = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return ""
    return str(claims.get("jti") or "").strip() if isinstance(claims, dict) else ""


def _canonical_fingerprint(path: str, payload: dict[str, Any]) -> str:
    normalized = dict(payload)
    metadata = normalized.get("metadata")
    if isinstance(metadata, dict):
        metadata = dict(metadata)
        metadata.pop("request_id", None)
        metadata.pop("idempotency_key", None)
        metadata.pop("idempotency_fingerprint", None)
        normalized["metadata"] = metadata
    encoded = json.dumps(
        {"path": path, "payload": normalized},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _read_body(receive: Any) -> tuple[bytes, Any]:
    body = bytearray()
    more = True
    while more:
        message = await receive()
        body.extend(message.get("body", b""))
        more = bool(message.get("more_body", False))
    sent = False

    async def replay() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    return bytes(body), replay


def _encoded_receive(payload: dict[str, Any]) -> Any:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    return receive


class RequestIdempotencyLedger:
    def __init__(self, database_url: str, ttl_seconds: int = 86400) -> None:
        self.path = _database_path(database_url)
        self.in_memory = str(self.path) == ":memory:"
        self.ttl_seconds = max(300, ttl_seconds)
        self._memory: sqlite3.Connection | None = None
        if not self.in_memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self.in_memory:
            if self._memory is None:
                self._memory = sqlite3.connect(":memory:", timeout=5, check_same_thread=False)
                self._memory.row_factory = sqlite3.Row
            return self._memory
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inference_request_idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    status_code INTEGER,
                    detail TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    expires_at_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_inference_idempotency_expiry "
                "ON inference_request_idempotency(expires_at_ms)"
            )
            conn.commit()
        finally:
            if not self.in_memory:
                conn.close()

    def reserve(self, key: str, fingerprint: str, request_id: str) -> tuple[bool, dict[str, Any]]:
        now = int(time.time() * 1000)
        expires = now + self.ttl_seconds * 1000
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM inference_request_idempotency WHERE expires_at_ms <= ?",
                (now,),
            )
            row = conn.execute(
                "SELECT * FROM inference_request_idempotency WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO inference_request_idempotency (
                        idempotency_key, fingerprint, request_id, state,
                        status_code, detail, created_at_ms, updated_at_ms, expires_at_ms
                    ) VALUES (?, ?, ?, 'in_progress', NULL, NULL, ?, ?, ?)
                    """,
                    (key, fingerprint, request_id, now, now, expires),
                )
                conn.commit()
                return True, {
                    "idempotency_key": key,
                    "fingerprint": fingerprint,
                    "request_id": request_id,
                    "state": "in_progress",
                    "status_code": None,
                }
            conn.commit()
            return False, dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            if not self.in_memory:
                conn.close()

    def get(self, key: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM inference_request_idempotency WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            if not self.in_memory:
                conn.close()

    def transition(
        self,
        key: str,
        state: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        if state not in _TERMINAL | {"in_progress", "upstream_started"}:
            raise ValueError(f"unsupported idempotency state {state}")
        now = int(time.time() * 1000)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state FROM inference_request_idempotency WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.rollback()
                return
            current = str(row["state"])
            if current == "completed" and state != "completed":
                conn.rollback()
                return
            if current == "possibly_accepted" and state in {"failed", "cancelled"}:
                conn.rollback()
                return
            conn.execute(
                """
                UPDATE inference_request_idempotency
                SET state = ?, status_code = ?, detail = ?, updated_at_ms = ?
                WHERE idempotency_key = ?
                """,
                (state, status_code, (detail or "")[:1000] or None, now, key),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if not self.in_memory:
                conn.close()


class RequestIdempotencyMiddleware:
    def __init__(self, app: Any, ledger: RequestIdempotencyLedger) -> None:
        self.app = app
        self.ledger = ledger

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path") or "")
        if (
            scope.get("type") != "http"
            or str(scope.get("method") or "GET").upper() != "POST"
            or path not in _PROTECTED_PATHS
        ):
            await self.app(scope, receive, send)
            return

        body, replay = await _read_body(receive)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            await self.app(scope, replay, send)
            return
        if not isinstance(payload, dict):
            await self.app(scope, replay, send)
            return

        fingerprint = _canonical_fingerprint(path, payload)
        explicit = _header(scope, b"idempotency-key")
        authorization = _header(scope, b"authorization")
        bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        jti = _token_jti(bearer)
        principal = hashlib.sha256(bearer.encode("utf-8")).hexdigest()[:16] if bearer else "anonymous"
        if explicit:
            if len(explicit) > 200:
                await JSONResponse(
                    status_code=400,
                    content={"detail": "Idempotency-Key exceeds 200 characters"},
                )(scope, replay, send)
                return
            key_material = f"explicit:{principal}:{explicit}"
        elif jti:
            key_material = f"claim:{jti}:{path}:{fingerprint}"
        else:
            key_material = ""

        if not key_material:
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            payload["metadata"] = {**metadata, "request_id": str(uuid.uuid4())}
            await self.app(scope, _encoded_receive(payload), send)
            return

        key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"auto-router:{key}"))
        created, record = self.ledger.reserve(key, fingerprint, request_id)
        if not created:
            if str(record.get("fingerprint")) != fingerprint:
                await JSONResponse(
                    status_code=409,
                    content={
                        "detail": "Idempotency-Key was already used for a different request",
                        "request_id": record.get("request_id"),
                        "state": record.get("state"),
                    },
                )(scope, replay, send)
                return
            await JSONResponse(
                status_code=409,
                content={
                    "detail": "duplicate inference request was not forwarded",
                    "request_id": record.get("request_id"),
                    "state": record.get("state"),
                    "status_code": record.get("status_code"),
                },
            )(scope, replay, send)
            return

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        payload["metadata"] = {
            **metadata,
            "request_id": request_id,
            "idempotency_key": key,
            "idempotency_fingerprint": fingerprint,
        }
        response_status: int | None = None
        response_started = False

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal response_status, response_started
            if message.get("type") == "http.response.start":
                response_started = True
                response_status = int(message.get("status") or 0)
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                current = self.ledger.get(key) or {}
                current_state = str(current.get("state") or "")
                if current_state not in {"completed", "possibly_accepted"}:
                    if response_status is not None and response_status < 400:
                        self.ledger.transition(key, "completed", status_code=response_status)
                    elif current_state == "upstream_started":
                        self.ledger.transition(
                            key,
                            "possibly_accepted",
                            status_code=response_status,
                            detail="upstream dispatch began before an unsuccessful final response",
                        )
                    else:
                        self.ledger.transition(key, "failed", status_code=response_status)
            await send(message)

        try:
            await self.app(scope, _encoded_receive(payload), tracked_send)
        except BaseException as exc:
            current = self.ledger.get(key) or {}
            if response_started or str(current.get("state")) == "upstream_started":
                self.ledger.transition(
                    key,
                    "possibly_accepted",
                    status_code=response_status,
                    detail=f"response interrupted after upstream dispatch: {type(exc).__name__}",
                )
            else:
                self.ledger.transition(
                    key,
                    "failed",
                    status_code=response_status,
                    detail=type(exc).__name__,
                )
            raise


def install_request_idempotency(app: Any, state: Any, main_module: Any) -> None:
    if getattr(app.state, "request_idempotency_installed", False):
        return
    settings = get_settings()
    ledger = RequestIdempotencyLedger(
        settings.database_url,
        ttl_seconds=int(getattr(settings, "idempotency_ttl_seconds", 86400)),
    )
    state.request_idempotency = ledger
    original_router_request = main_module._router_request

    def router_request(route: str, body: dict[str, Any]):
        request = original_router_request(route, body)
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        supplied = str(metadata.get("request_id") or "").strip()
        if supplied:
            request.request_id = supplied
        return request

    main_module._router_request = router_request
    app.add_middleware(RequestIdempotencyMiddleware, ledger=ledger)
    app.state.request_idempotency_installed = True
