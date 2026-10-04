"""Endpoint robustness: capability degradation and transient retry.

A private OpenAI-compatible runtime is not obliged to implement every part of
the specification. These tests use loopback handlers that reject specific
parameters, stall, or fail transiently, so the recovery paths are exercised
without touching a real node.

The invariant under test throughout: a request that succeeds after degradation
must *say so*. Silently succeeding with a narrower request would make the
evidence a lie.
"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

from test_realtask_support import HarnessTestCase  # noqa: F401  (path bootstrap)

from realtask.adapter import (
    REJECTION_CODES,
    TRANSIENT_CODES,
    AdapterError,
    ChatRequest,
    EndpointConfig,
    OpenAIChatAdapter,
)
from realtask.taxonomy import Outcome

GOOD_PAYLOAD = {
    "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
}


class RuleHandler(BaseHTTPRequestHandler):
    """Applies a scripted sequence of responses, recording every request."""

    protocol_version = "HTTP/1.1"
    rules: List[Dict[str, Any]] = []
    seen: List[Dict[str, Any]] = []

    def log_message(self, *_args):
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}
        type(self).seen.append(payload)

        rule = type(self).rules[min(len(type(self).seen) - 1, len(type(self).rules) - 1)]
        action = rule["action"]
        if action == "reject":
            self._json(rule["code"], {"error": {"message": rule["message"]}})
            return
        if action == "transient":
            self._json(rule["code"], {"error": {"message": rule["message"]}})
            return
        if payload.get("stream"):
            self._stream_ok()
            return
        self._json(200, GOOD_PAYLOAD)

    def _stream_ok(self):
        """Serve the SSE shape, so a streaming request is answered honestly."""
        chunks = [
            {"choices": [{"delta": {"content": part}, "finish_reason": None}]}
            for part in ('{"ok"', ": true}")
        ]
        chunks.append(
            {
                "choices": [{"delta": {"content": ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in chunks:
            self._chunk(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self._chunk(b"data: [DONE]\n\n")
        self._chunk(b"")

    def _chunk(self, payload: bytes):
        self.wfile.write("{:X}\r\n".format(len(payload)).encode() + payload + b"\r\n")
        self.wfile.flush()

    def _json(self, code: int, payload: Dict[str, Any]):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RuleEndpoint:
    """A loopback endpoint that replays a scripted rule sequence."""

    def __init__(self, rules):
        handler = type(
            "BoundRuleHandler", (RuleHandler,), {"rules": list(rules), "seen": []}
        )
        self.handler = handler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return "http://{}:{}/v1".format(host, port)

    @property
    def requests(self) -> List[Dict[str, Any]]:
        return self.handler.seen

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def reject(message: str, code: int = 400):
    return {"action": "reject", "code": code, "message": message}


def transient(message: str = "busy", code: int = 503):
    return {"action": "transient", "code": code, "message": message}


def ok():
    return {"action": "ok"}


class DegradationTests(HarnessTestCase):
    def adapter(self, endpoint, **overrides):
        config = EndpointConfig(
            label="rules",
            base_url=endpoint.base_url,
            model="m",
            timeout_s=10,
            retry_backoff_s=0.0,
            **overrides,
        )
        return OpenAIChatAdapter(config)

    def call(self, adapter):
        return adapter.complete(ChatRequest(role="single", system="s", user="u"))

    def test_rejects_stream_options_then_succeeds(self):
        endpoint = RuleEndpoint([reject("unknown parameter: stream_options"), ok()])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint)
        response = self.call(adapter)
        self.assertIn('"ok": true', response.content)
        self.assertTrue(response.stream_used)
        self.assertFalse(response.request_profile["stream_options"])
        self.assertTrue(response.request_profile["stream"])
        self.assertIn("stream_options", adapter.degradations()[0])

    def test_rejects_seed_then_succeeds(self):
        endpoint = RuleEndpoint([reject("seed is not supported"), ok()])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint)
        response = self.call(adapter)
        self.assertNotIn("seed", endpoint.requests[1])
        self.assertFalse(response.request_profile["seed"])
        self.assertIn("seed", response.request_profile["degradations"][0])

    def test_rejects_streaming_then_falls_back_to_blocking(self):
        endpoint = RuleEndpoint([reject("streaming is not supported here"), ok()])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint)
        response = self.call(adapter)
        self.assertFalse(response.stream_used)
        self.assertIsNone(response.ttft_s)
        self.assertFalse(response.request_profile["stream"])
        self.assertFalse(endpoint.requests[1].get("stream"))
        self.assertIn("time-to-first-token", adapter.degradations()[0])

    def test_ladder_walks_through_several_rejections_in_order(self):
        endpoint = RuleEndpoint(
            [
                reject("unrecognised parameter stream_options"),
                reject("parameter seed not recognised"),
                reject("stream not supported"),
                ok(),
            ]
        )
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint)
        response = self.call(adapter)
        self.assertIn('"ok": true', response.content)
        self.assertFalse(response.stream_used, "streaming was dropped, so no SSE")
        self.assertEqual(len(endpoint.requests), 4)
        profile = response.request_profile
        self.assertFalse(profile["stream_options"])
        self.assertFalse(profile["seed"])
        self.assertFalse(profile["stream"])
        self.assertEqual(len(profile["degradations"]), 3)

    def test_profile_is_cached_across_calls(self):
        endpoint = RuleEndpoint([reject("stream_options"), ok(), ok()])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint)
        self.call(adapter)
        self.call(adapter)
        self.assertEqual(len(endpoint.requests), 3)
        self.assertNotIn(
            "stream_options",
            endpoint.requests[2],
            "the second call must reuse the learned profile, not re-probe",
        )

    def test_unrecognised_rejection_is_terminal_and_diagnosed(self):
        endpoint = RuleEndpoint([reject("model 'm' does not exist")])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint)
        with self.assertRaises(AdapterError) as ctx:
            self.call(adapter)
        self.assertIs(ctx.exception.outcome, Outcome.PROTOCOL_FAILURE)
        self.assertIn("does not exist", str(ctx.exception))
        self.assertIn("nothing further could be dropped", str(ctx.exception))
        self.assertEqual(adapter.degradations(), [])

    def test_terminal_4xx_other_than_400_is_not_degraded(self):
        endpoint = RuleEndpoint([reject("no such route", code=404)])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint)
        with self.assertRaises(AdapterError) as ctx:
            self.call(adapter)
        self.assertIs(ctx.exception.outcome, Outcome.PROTOCOL_FAILURE)
        self.assertEqual(adapter.degradations(), [])
        self.assertIn("404", str(ctx.exception))


class RetryTests(HarnessTestCase):
    def adapter(self, endpoint, **overrides):
        config = EndpointConfig(
            label="retry",
            base_url=endpoint.base_url,
            model="m",
            timeout_s=10,
            retry_backoff_s=0.0,
            **overrides,
        )
        return OpenAIChatAdapter(config)

    def call(self, adapter):
        return adapter.complete(ChatRequest(role="single", system="s", user="u"))

    def test_transient_failure_is_retried_once(self):
        endpoint = RuleEndpoint([transient(), ok()])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint)
        response = self.call(adapter)
        self.assertEqual(response.retries, 1)
        self.assertEqual(len(endpoint.requests), 2)

    def test_retry_budget_is_bounded(self):
        endpoint = RuleEndpoint([transient(), transient(), transient(), ok()])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint, max_retries=1)
        with self.assertRaises(AdapterError) as ctx:
            self.call(adapter)
        self.assertIs(ctx.exception.outcome, Outcome.PROTOCOL_FAILURE)
        self.assertEqual(len(endpoint.requests), 2, "one original attempt plus one retry")

    def test_retry_can_be_disabled(self):
        endpoint = RuleEndpoint([transient(), ok()])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint, max_retries=0)
        with self.assertRaises(AdapterError):
            self.call(adapter)
        self.assertEqual(len(endpoint.requests), 1)

    def test_degradation_does_not_consume_the_retry_budget(self):
        endpoint = RuleEndpoint([reject("stream_options"), transient(), ok()])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint, max_retries=1)
        response = self.call(adapter)
        self.assertEqual(response.retries, 1)
        self.assertEqual(len(endpoint.requests), 3)

    def test_retry_is_recorded_in_the_profile(self):
        endpoint = RuleEndpoint([transient(), ok()])
        self.addCleanup(endpoint.close)
        adapter = self.adapter(endpoint)
        response = self.call(adapter)
        self.assertTrue(
            any("transient failure" in d for d in response.request_profile["degradations"]),
            response.request_profile["degradations"],
        )


class CodeSetTests(unittest.TestCase):
    def test_transient_and_rejection_sets_are_disjoint(self):
        self.assertEqual(REJECTION_CODES & TRANSIENT_CODES, frozenset())

    def test_retryable_codes_are_5xx_or_retry_signalled(self):
        self.assertTrue(TRANSIENT_CODES <= {408, 409, 425, 429, 500, 502, 503, 504})
        for code in (400, 401, 403, 404, 422):
            self.assertNotIn(code, TRANSIENT_CODES, code)

    def test_default_retry_budget_is_conservative(self):
        config = EndpointConfig(label="l", base_url="http://a/v1", model="m")
        self.assertEqual(config.max_retries, 1)
        self.assertIn("max_retries", config.identity())


class UnreachableEndpointTests(HarnessTestCase):
    def test_unreachable_endpoint_is_reported_clearly(self):
        # Port 9 is discard; nothing listens, so the connection is refused.
        adapter = OpenAIChatAdapter(
            EndpointConfig(
                label="dead",
                base_url="http://127.0.0.1:9/v1",
                model="m",
                timeout_s=2,
                retry_backoff_s=0.0,
                max_retries=0,
            )
        )
        with self.assertRaises(AdapterError) as ctx:
            adapter.complete(ChatRequest(role="single", system="s", user="u"))
        self.assertIn("unreachable", str(ctx.exception))
        self.assertIs(ctx.exception.outcome, Outcome.PROTOCOL_FAILURE)


if __name__ == "__main__":
    unittest.main()
