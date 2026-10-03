"""Model invocation behind a narrow adapter.

The benchmark talks to exactly one kind of runtime: a private,
OpenAI-compatible chat-completions endpoint that somebody else has already
started. This module never discovers hosts, never probes a subnet, never starts
a server, and never reads fleet topology. It POSTs to the URL it is given.

Node identity is an operator-supplied *label*, not a hardcoded list. Any
OpenAI-compatible endpoint can be pointed at without changing benchmark
semantics; the harness has no idea or opinion about what the label refers to.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence

from .taxonomy import Outcome


class AdapterError(RuntimeError):
    """Model invocation failed in a way that maps onto the taxonomy."""

    def __init__(self, message: str, outcome: Outcome):
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class EndpointConfig:
    """One already-running OpenAI-compatible endpoint.

    ``label`` is free-form operator text used only to attribute metrics
    ("optiplex", "destroyer", a laptop name). It is never interpreted.
    """

    label: str
    base_url: str
    model: str
    api_key: Optional[str] = None
    timeout_s: float = 120.0
    temperature: float = 0.0
    seed: Optional[int] = 13
    max_tokens: int = 1600
    stream: bool = True
    node: str = ""

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("EndpointConfig.base_url is required")
        if not self.model:
            raise ValueError("EndpointConfig.model is required")

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def identity(self) -> Dict[str, Any]:
        """Evidence-safe identity. The API key is reduced to a boolean."""
        return {
            "label": self.label,
            "node": self.node,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_set": bool(self.api_key),
            "timeout_s": self.timeout_s,
            "temperature": self.temperature,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
            "stream": self.stream,
        }

    def redacted(self) -> "EndpointConfig":
        return replace(self, api_key=None) if self.api_key else self


def endpoint_from_config_file(path: str) -> EndpointConfig:
    """Load one endpoint from a private JSON config file.

    The file may hold a bare object or ``{"endpoints": {...}}``. The API key is
    read from ``api_key_env`` (an environment variable name) rather than being
    stored in the file, so configs are safe to keep out of evidence.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and "endpoints" in payload:
        name = payload.get("default_endpoint")
        if not name:
            names = sorted(payload["endpoints"])
            if len(names) != 1:
                raise ValueError(
                    "config has {} endpoints; set 'default_endpoint'".format(len(names))
                )
            name = names[0]
        entry = payload["endpoints"][name]
        label = entry.get("label", name)
    else:
        entry = payload
        label = payload.get("label", "default")

    api_key = None
    env_name = entry.get("api_key_env")
    if env_name:
        api_key = os.environ.get(env_name)
    elif entry.get("api_key"):
        api_key = entry["api_key"]

    return EndpointConfig(
        label=str(label),
        base_url=str(entry["base_url"]),
        model=str(entry["model"]),
        api_key=api_key,
        timeout_s=float(entry.get("timeout_s", 120.0)),
        temperature=float(entry.get("temperature", 0.0)),
        seed=entry.get("seed", 13),
        max_tokens=int(entry.get("max_tokens", 1600)),
        stream=bool(entry.get("stream", True)),
        node=str(entry.get("node", "")),
    )


def endpoint_from_env(prefix: str = "REALTASK_ENDPOINT") -> EndpointConfig:
    """Build an endpoint from ``<PREFIX>_BASE_URL`` / ``<PREFIX>_MODEL`` / ..."""
    base = os.environ.get(prefix + "_BASE_URL")
    model = os.environ.get(prefix + "_MODEL")
    if not base or not model:
        raise ValueError(
            "set {0}_BASE_URL and {0}_MODEL to point the harness at a running endpoint".format(
                prefix
            )
        )
    return EndpointConfig(
        label=os.environ.get(prefix + "_LABEL", "default"),
        base_url=base,
        model=model,
        api_key=os.environ.get(prefix + "_API_KEY"),
        timeout_s=float(os.environ.get(prefix + "_TIMEOUT_S", "120")),
        temperature=float(os.environ.get(prefix + "_TEMPERATURE", "0")),
        seed=int(os.environ.get(prefix + "_SEED", "13")),
        max_tokens=int(os.environ.get(prefix + "_MAX_TOKENS", "1600")),
        stream=os.environ.get(prefix + "_STREAM", "1") not in ("0", "false", "False"),
        node=os.environ.get(prefix + "_NODE", ""),
    )


@dataclass
class ChatRequest:
    """One bounded, single-turn role call."""

    role: str
    system: str
    user: str


@dataclass
class ChatResponse:
    """One role call's result plus the performance measurements we care about."""

    role: str
    content: str
    finish_reason: Optional[str]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
    usage_reported: bool
    wall_s: float
    ttft_s: Optional[float]
    tokens_per_s: Optional[float]
    prompt_tokens_per_s: Optional[float]
    server_timings: Dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    identity: Dict[str, Any] = field(default_factory=dict)
    stream_used: bool = False
    raw_request_sha256: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "finish_reason": self.finish_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "usage_reported": self.usage_reported,
            "wall_s": self.wall_s,
            "ttft_s": self.ttft_s,
            "tokens_per_s": self.tokens_per_s,
            "prompt_tokens_per_s": self.prompt_tokens_per_s,
            "server_timings": self.server_timings,
            "started_at": self.started_at,
            "identity": self.identity,
            "stream_used": self.stream_used,
            "raw_request_sha256": self.raw_request_sha256,
        }


class ChatAdapter:
    """Interface the runner depends on. Tests substitute a scripted adapter."""

    identity: Dict[str, Any] = {"label": "abstract", "model": "abstract"}

    def complete(self, request: ChatRequest) -> ChatResponse:  # pragma: no cover
        raise NotImplementedError


def _build_payload(config: EndpointConfig, request: ChatRequest) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "stream": bool(config.stream),
    }
    if config.seed is not None:
        payload["seed"] = config.seed
    if config.stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _usage_from(chunk: Dict[str, Any]) -> Dict[str, Optional[int]]:
    usage = chunk.get("usage") or {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    if total is None and isinstance(prompt, int) and isinstance(completion, int):
        total = prompt + completion
    return {
        "prompt_tokens": prompt if isinstance(prompt, int) else None,
        "completion_tokens": completion if isinstance(completion, int) else None,
        "total_tokens": total if isinstance(total, int) else None,
    }


class OpenAIChatAdapter(ChatAdapter):
    """POSTs to an already-running OpenAI-compatible endpoint.

    Streaming is used by default so that time-to-first-token is a real
    measurement rather than a guess. Token counts come from the endpoint's
    ``usage`` block; when the endpoint omits it the harness records ``None``
    and flags ``usage_reported: false`` instead of inventing a number.
    """

    def __init__(self, config: EndpointConfig):
        self.config = config
        self.identity = dict(config.identity(), adapter="openai-compatible")

    def _request(self, url: str, payload: Dict[str, Any], timeout: float):
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "*/*"}
        if self.config.api_key:
            headers["Authorization"] = "Bearer " + self.config.api_key
        return urllib.request.Request(url, data=body, headers=headers, method="POST")

    def complete(self, request: ChatRequest) -> ChatResponse:
        import hashlib

        payload = _build_payload(self.config, request)
        raw_sha = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        started_wall = time.time()
        start = time.monotonic()
        if self.config.stream:
            try:
                return self._complete_stream(request, payload, raw_sha, start, started_wall)
            except AdapterError as exc:
                if exc.outcome is not Outcome.TIMEOUT:
                    raise
                raise
        return self._complete_blocking(request, payload, raw_sha, start, started_wall)

    def _complete_stream(
        self,
        request: ChatRequest,
        payload: Dict[str, Any],
        raw_sha: str,
        start: float,
        started_wall: float,
    ) -> ChatResponse:
        import hashlib

        chunks: List[str] = []
        ttft: Optional[float] = None
        finish_reason: Optional[str] = None
        usage: Dict[str, Optional[int]] = {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
        server_timings: Dict[str, Any] = {}
        http = self._request(self.config.chat_url, payload, self.config.timeout_s)
        try:
            with urllib.request.urlopen(http, timeout=self.config.timeout_s) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(chunk.get("timings"), dict):
                        server_timings = dict(chunk["timings"])
                    chunk_usage = _usage_from(chunk)
                    if chunk_usage["total_tokens"] is not None:
                        usage = chunk_usage
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content") or ""
                    if piece:
                        if ttft is None:
                            ttft = time.monotonic() - start
                        chunks.append(piece)
        except (urllib.error.HTTPError, ) as exc:
            raise AdapterError(
                "endpoint returned HTTP {}: {}".format(exc.code, _short(exc.read())),
                Outcome.PROTOCOL_FAILURE,
            ) from exc
        except socket.timeout as exc:
            raise AdapterError("endpoint read timed out", Outcome.TIMEOUT) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                raise AdapterError("endpoint read timed out", Outcome.TIMEOUT) from exc
            raise AdapterError(
                "endpoint unreachable: {}".format(reason), Outcome.PROTOCOL_FAILURE
            ) from exc

        elapsed = time.monotonic() - start
        content = "".join(chunks)
        completion = usage["completion_tokens"]
        rate = (completion / elapsed) if (completion and elapsed > 0) else None
        prompt_rate = None
        if usage["prompt_tokens"] and ttft:
            prompt_rate = usage["prompt_tokens"] / ttft
        return ChatResponse(
            role=request.role,
            content=content,
            finish_reason=finish_reason,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=completion,
            total_tokens=usage["total_tokens"],
            usage_reported=usage["total_tokens"] is not None,
            wall_s=elapsed,
            ttft_s=ttft,
            tokens_per_s=rate,
            prompt_tokens_per_s=prompt_rate,
            server_timings=server_timings,
            started_at=started_wall,
            identity=self.identity,
            stream_used=True,
            raw_request_sha256=raw_sha,
        )

    def _complete_blocking(
        self,
        request: ChatRequest,
        payload: Dict[str, Any],
        raw_sha: str,
        start: float,
        started_wall: float,
    ) -> ChatResponse:
        payload = dict(payload)
        payload["stream"] = False
        payload.pop("stream_options", None)
        http = self._request(self.config.chat_url, payload, self.config.timeout_s)
        try:
            with urllib.request.urlopen(http, timeout=self.config.timeout_s) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raise AdapterError(
                "endpoint returned HTTP {}: {}".format(exc.code, _short(exc.read())),
                Outcome.PROTOCOL_FAILURE,
            ) from exc
        except socket.timeout as exc:
            raise AdapterError("endpoint timed out", Outcome.TIMEOUT) from exc
        except urllib.error.URLError as exc:
            raise AdapterError(
                "endpoint unreachable: {}".format(getattr(exc, "reason", exc)),
                Outcome.PROTOCOL_FAILURE,
            ) from exc
        except json.JSONDecodeError as exc:
            raise AdapterError(
                "endpoint returned non-JSON body", Outcome.PROTOCOL_FAILURE
            ) from exc

        elapsed = time.monotonic() - start
        choices = data.get("choices") or []
        content = ""
        finish_reason: Optional[str] = None
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            finish_reason = choices[0].get("finish_reason")
        usage = _usage_from(data)
        completion = usage["completion_tokens"]
        return ChatResponse(
            role=request.role,
            content=content,
            finish_reason=finish_reason,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=completion,
            total_tokens=usage["total_tokens"],
            usage_reported=usage["total_tokens"] is not None,
            wall_s=elapsed,
            ttft_s=None,
            tokens_per_s=(completion / elapsed) if (completion and elapsed > 0) else None,
            prompt_tokens_per_s=None,
            server_timings=data.get("timings") if isinstance(data.get("timings"), dict) else {},
            started_at=started_wall,
            identity=self.identity,
            stream_used=False,
            raw_request_sha256=raw_sha,
        )


def _short(payload: bytes, limit: int = 400) -> str:
    text = payload.decode("utf-8", errors="replace")
    return text[:limit]


@dataclass
class ScriptedResponse:
    """One canned reply for :class:`ScriptedAdapter`."""

    content: str
    finish_reason: str = "stop"
    prompt_tokens: int = 100
    completion_tokens: int = 50
    ttft_s: Optional[float] = 0.5
    tokens_per_s: Optional[float] = 20.0
    raise_error: Optional[AdapterError] = None


class ScriptedAdapter(ChatAdapter):
    """Deterministic adapter used by the test suite.

    It records every request it was given, so tests can assert on the exact
    payload the reviewer received.
    """

    def __init__(self, responses: Sequence[ScriptedResponse], identity: Optional[Dict[str, Any]] = None):
        self._responses = list(responses)
        self.requests: List[ChatRequest] = []
        self.identity = identity or {
            "label": "scripted",
            "node": "none",
            "model": "scripted",
            "adapter": "scripted",
        }

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if not self._responses:
            raise AdapterError("scripted adapter exhausted", Outcome.PROTOCOL_FAILURE)
        canned = self._responses.pop(0)
        if canned.raise_error is not None:
            raise canned.raise_error
        import hashlib

        return ChatResponse(
            role=request.role,
            content=canned.content,
            finish_reason=canned.finish_reason,
            prompt_tokens=canned.prompt_tokens,
            completion_tokens=canned.completion_tokens,
            total_tokens=canned.prompt_tokens + canned.completion_tokens,
            usage_reported=True,
            wall_s=1.0,
            ttft_s=canned.ttft_s,
            tokens_per_s=canned.tokens_per_s,
            prompt_tokens_per_s=100.0,
            identity=self.identity,
            stream_used=True,
            raw_request_sha256=hashlib.sha256(request.user.encode("utf-8")).hexdigest(),
        )
