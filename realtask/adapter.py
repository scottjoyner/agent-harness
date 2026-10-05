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


class _EndpointRejected(Exception):
    """The endpoint refused this request *shape*.

    Almost always an optional parameter it does not implement. Carries the body
    so the caller can decide what, if anything, to drop.
    """

    def __init__(self, code: int, body: str):
        super().__init__("HTTP {}: {}".format(code, body[:400]))
        self.code = code
        self.body = body


class _TransientFailure(Exception):
    """A failure that may simply not recur: timeout, reset connection, 5xx."""

    def __init__(self, message: str, is_timeout: bool = False):
        super().__init__(message)
        self.is_timeout = is_timeout


#: HTTP codes worth a second attempt. Deliberately excludes 4xx: a rejected
#: request is a configuration problem, and retrying only burns the deadline.
TRANSIENT_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

#: Codes meaning "this request shape is not acceptable here". Only these drive
#: the capability ladder.
REJECTION_CODES = frozenset({400, 422})


class AdapterError(RuntimeError):
    """Model invocation failed in a way that maps onto the taxonomy."""

    def __init__(self, message: str, outcome: Outcome):
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class EndpointConfig:
    """One already-running OpenAI-compatible endpoint.

    ``label`` and ``node`` are free-form operator text used only to attribute
    metrics to whatever runtime the operator pointed at. The harness has no list
    of known runtimes, never interprets these strings, and must never grow one.
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
    max_retries: int = 1
    retry_backoff_s: float = 0.5

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
            "max_retries": self.max_retries,
            "retry_backoff_s": self.retry_backoff_s,
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
    #: What was actually asked of the endpoint, after any degradation, and why.
    request_profile: Dict[str, Any] = field(default_factory=dict)
    retries: int = 0

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
            "request_profile": self.request_profile,
            "retries": self.retries,
        }


class ChatAdapter:
    """Interface the runner depends on. Tests substitute a scripted adapter."""

    identity: Dict[str, Any] = {"label": "abstract", "model": "abstract"}

    def complete(self, request: ChatRequest) -> ChatResponse:  # pragma: no cover
        raise NotImplementedError


@dataclass
class RequestProfile:
    """What the harness asks of the endpoint, and what it had to give up.

    A private OpenAI-compatible runtime need not implement every part of the
    specification. Rather than probing -- which would edge into discovering
    runtimes the operator never mentioned -- the harness starts from the full
    request and narrows it only when the endpoint actively refuses it. The
    resulting profile is recorded on every call, so a real-endpoint run stays
    diagnosable instead of merely working.
    """

    stream: bool = True
    stream_options: bool = True
    seed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stream": self.stream,
            "stream_options": self.stream_options,
            "seed": self.seed,
        }


def build_payload(
    config: EndpointConfig, request: ChatRequest, profile: RequestProfile
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "stream": bool(profile.stream),
    }
    if config.seed is not None and profile.seed:
        payload["seed"] = config.seed
    if profile.stream and profile.stream_options:
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
        self._profile = RequestProfile(
            stream=bool(config.stream),
            stream_options=bool(config.stream),
            seed=config.seed is not None,
        )
        self._degradations: List[str] = []
        self.identity = dict(config.identity(), adapter="openai-compatible")

    def degradations(self) -> List[str]:
        """Everything the harness had to give up to get this endpoint to answer."""
        return list(self._degradations)

    def _request(self, url: str, payload: Dict[str, Any], timeout: float):
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "*/*"}
        if self.config.api_key:
            headers["Authorization"] = "Bearer " + self.config.api_key
        return urllib.request.Request(url, data=body, headers=headers, method="POST")

    def complete(self, request: ChatRequest) -> ChatResponse:
        """One bounded role call.

        Two independent recovery paths, neither of which guesses:

        * capability degradation -- on HTTP 400/422 the offending optional
          parameter is dropped and the call reissued with the narrower shape;
        * transient retry -- on timeout, connection reset or 5xx the call is
          retried up to ``max_retries`` times.

        Both record what they did. A degraded request that succeeds is not
        silently equivalent to the request we wanted to make.
        """
        import hashlib

        transient_retries = 0
        while True:
            payload = build_payload(self.config, request, self._profile)
            raw_sha = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode("utf-8")
            ).hexdigest()
            start = time.monotonic()
            started_wall = time.time()
            try:
                response = self._send(
                    request, payload, raw_sha, start, started_wall, transient_retries
                )
            except _EndpointRejected as rejected:
                if not self._degrade(rejected):
                    raise AdapterError(
                        "endpoint rejected the request and nothing further could be "
                        "dropped: {}".format(rejected),
                        Outcome.PROTOCOL_FAILURE,
                    ) from rejected
                continue
            except _TransientFailure as failure:
                if transient_retries >= max(0, self.config.max_retries):
                    raise AdapterError(
                        str(failure),
                        Outcome.TIMEOUT if failure.is_timeout else Outcome.PROTOCOL_FAILURE,
                    ) from failure
                transient_retries += 1
                self._degradations.append(
                    "transient failure, will retry: {}".format(failure)
                )
                if self.config.retry_backoff_s:
                    time.sleep(self.config.retry_backoff_s)
                continue
            profile = self._profile.to_dict()
            profile["degradations"] = list(self._degradations)
            response.request_profile = profile
            response.retries = transient_retries
            return response

    def _degrade(self, rejected: _EndpointRejected) -> bool:
        """Drop one unsupported parameter. True when the profile changed.

        Order matters: ``stream_options`` is checked before ``stream`` because
        the former contains the latter as a substring.
        """
        body = (rejected.body or "").lower()
        if self._profile.stream_options and (
            "stream_options" in body or "stream options" in body
        ):
            self._profile.stream_options = False
            self._degradations.append(
                "endpoint rejected stream_options; asking without it, so token "
                "usage may be absent"
            )
            return True
        if self._profile.seed and "seed" in body:
            self._profile.seed = False
            self._degradations.append(
                "endpoint rejected seed; determinism now rests on temperature alone"
            )
            return True
        if self._profile.stream and "stream" in body:
            self._profile.stream = False
            self._degradations.append(
                "endpoint rejected streaming; falling back to a blocking request, "
                "so time-to-first-token is not measurable"
            )
            return True
        return False

    def _send(
        self,
        request: ChatRequest,
        payload: Dict[str, Any],
        raw_sha: str,
        start: float,
        started_wall: float,
        retries: int,
    ) -> ChatResponse:
        if payload.get("stream"):
            return self._complete_stream(request, payload, raw_sha, start, started_wall)
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
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        # A server may answer 200 with a bare JSON error instead
                        # of an SSE frame. Same silent-empty outcome if skipped.
                        if line.startswith("{"):
                            try:
                                bare = json.loads(line)
                            except json.JSONDecodeError:
                                bare = None
                            if isinstance(bare, dict) and isinstance(
                                bare.get("error"), (dict, str)
                            ):
                                raise _EndpointRejected(
                                    400, json.dumps(bare["error"])[:600]
                                )
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # A server-side error delivered over SSE arrives as
                    # ``event: error`` followed by ``data: {"error": {...}}``.
                    # Some servers answer HTTP 200 and only then report the
                    # refusal in-band -- a context overflow looks exactly like
                    # this. Skipping it (there are no ``choices``) made the
                    # attempt look like a model that returned nothing, which is
                    # a false record: the truth was that the request was
                    # refused. Surface it as a rejection so the caller's
                    # degrade ladder can drop a parameter or report the reason.
                    if isinstance(chunk.get("error"), (dict, str)):
                        raise _EndpointRejected(
                            400, json.dumps(chunk["error"])[:600]
                        )
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
        except urllib.error.HTTPError as exc:
            raise _classify_http(exc.code, _short(exc.read())) from exc
        except socket.timeout as exc:
            raise _TransientFailure("endpoint read timed out", is_timeout=True) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                raise _TransientFailure("endpoint read timed out", is_timeout=True) from exc
            raise _TransientFailure("endpoint unreachable: {}".format(reason)) from exc

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
        http = self._request(self.config.chat_url, payload, self.config.timeout_s)
        try:
            with urllib.request.urlopen(http, timeout=self.config.timeout_s) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raise _classify_http(exc.code, _short(exc.read())) from exc
        except socket.timeout as exc:
            raise _TransientFailure("endpoint timed out", is_timeout=True) from exc
        except urllib.error.URLError as exc:
            raise _TransientFailure(
                "endpoint unreachable: {}".format(getattr(exc, "reason", exc))
            ) from exc
        except json.JSONDecodeError as exc:
            raise AdapterError(
                "endpoint returned a non-JSON body: {}".format(exc)[:400],
                Outcome.PROTOCOL_FAILURE,
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


def _classify_http(code: int, body: str):
    """Route an HTTP failure to the right recovery path.

    400/422 mean the request shape is unacceptable here, which the capability
    ladder can narrow. 5xx and friends may simply not have happened yet. Anything
    else is a real configuration problem and must surface rather than be retried.
    """
    if code in REJECTION_CODES:
        return _EndpointRejected(code, body)
    if code in TRANSIENT_CODES:
        return _TransientFailure("endpoint returned HTTP {}: {}".format(code, body))
    return AdapterError(
        "endpoint returned HTTP {}: {}".format(code, body), Outcome.PROTOCOL_FAILURE
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
