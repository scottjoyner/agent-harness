"""Endpoint configuration tests.

The harness is pointed at a private OpenAI-compatible endpoint. How that
endpoint is supplied -- argv, a config file, or the environment -- is an
operator's choice and must not change benchmark semantics. These tests pin that
the three inputs agree, and that a secret never reaches the evidence.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from test_realtask_support import REPO_ROOT, HarnessTestCase  # noqa: F401

from realtask.adapter import (
    EndpointConfig,
    endpoint_from_config_file,
    endpoint_from_env,
)
from realtask.version import MAX_REFINEMENTS  # noqa: F401


class EndpointConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="realtask-endpoint-")
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write_config(self, payload, name="endpoint.json"):
        path = self.tmp / name
        path.write_text(json.dumps(payload, indent=2))
        return path

    def test_bare_object_config(self):
        path = self.write_config(
            {"base_url": "http://10.0.0.5:1234/v1", "model": "some-model", "label": "lab"}
        )
        config = endpoint_from_config_file(str(path))
        self.assertEqual(config.base_url, "http://10.0.0.5:1234/v1")
        self.assertEqual(config.model, "some-model")
        self.assertEqual(config.label, "lab")
        self.assertEqual(config.chat_url, "http://10.0.0.5:1234/v1/chat/completions")

    def test_named_endpoints_config(self):
        path = self.write_config(
            {
                "default_endpoint": "primary",
                "endpoints": {
                    "primary": {"base_url": "http://a:1/v1", "model": "m1", "node": "n1"},
                    "other": {"base_url": "http://b:2/v1", "model": "m2"},
                },
            }
        )
        config = endpoint_from_config_file(str(path))
        self.assertEqual(config.base_url, "http://a:1/v1")
        self.assertEqual(config.node, "n1")

    def test_single_named_endpoint_is_selected_without_a_default(self):
        path = self.write_config(
            {"endpoints": {"only": {"base_url": "http://a:1/v1", "model": "m"}}}
        )
        self.assertEqual(endpoint_from_config_file(str(path)).model, "m")

    def test_ambiguous_endpoint_set_is_rejected(self):
        path = self.write_config(
            {"endpoints": {"a": {"base_url": "http://a/v1", "model": "m"},
                           "b": {"base_url": "http://b/v1", "model": "m"}}}
        )
        with self.assertRaises(ValueError) as ctx:
            endpoint_from_config_file(str(path))
        self.assertIn("default_endpoint", str(ctx.exception))

    def test_api_key_comes_from_the_environment_not_the_file(self):
        path = self.write_config(
            {"base_url": "http://a:1/v1", "model": "m", "api_key_env": "MY_ENDPOINT_KEY"}
        )
        os.environ.pop("MY_ENDPOINT_KEY", None)
        self.assertIsNone(endpoint_from_config_file(str(path)).api_key)
        self.assertFalse(endpoint_from_config_file(str(path)).identity()["api_key_set"])

        os.environ["MY_ENDPOINT_KEY"] = "sk-live-value"
        self.addCleanup(os.environ.pop, "MY_ENDPOINT_KEY", None)
        config = endpoint_from_config_file(str(path))
        self.assertEqual(config.api_key, "sk-live-value")

        # The file itself must not be able to leak the value into evidence.
        identity = json.dumps(config.identity())
        self.assertNotIn("sk-live-value", identity)
        self.assertNotIn("api_key", identity.replace("api_key_set", ""))
        self.assertTrue(config.identity()["api_key_set"])

    def test_sampling_defaults_are_deterministic(self):
        path = self.write_config({"base_url": "http://a:1/v1", "model": "m"})
        config = endpoint_from_config_file(str(path))
        self.assertEqual(config.temperature, 0.0)
        self.assertEqual(config.seed, 13)
        self.assertTrue(config.stream)
        self.assertEqual(config.timeout_s, 120.0)

    def test_env_input_matches_file_input(self):
        path = self.write_config(
            {"label": "lab", "base_url": "http://10.0.0.5:1234/v1", "model": "some-model",
             "timeout_s": 90, "max_tokens": 512, "stream": False}
        )
        from_file = endpoint_from_config_file(str(path))

        for key, value in (
            ("REALTASK_ENDPOINT_BASE_URL", "http://10.0.0.5:1234/v1"),
            ("REALTASK_ENDPOINT_MODEL", "some-model"),
            ("REALTASK_ENDPOINT_LABEL", "lab"),
            ("REALTASK_ENDPOINT_TIMEOUT_S", "90"),
            ("REALTASK_ENDPOINT_MAX_TOKENS", "512"),
            ("REALTASK_ENDPOINT_STREAM", "0"),
            ("REALTASK_ENDPOINT_TEMPERATURE", "0"),
            ("REALTASK_ENDPOINT_SEED", "13"),
        ):
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)

        from_env = endpoint_from_env()
        self.assertEqual(from_env.identity(), from_file.identity())

    def test_missing_env_input_is_a_clear_error(self):
        for key in ("REALTASK_ENDPOINT_BASE_URL", "REALTASK_ENDPOINT_MODEL"):
            os.environ.pop(key, None)
        with self.assertRaises(ValueError) as ctx:
            endpoint_from_env()
        self.assertIn("REALTASK_ENDPOINT_BASE_URL", str(ctx.exception))

    def test_trailing_slash_is_normalised(self):
        self.assertEqual(
            EndpointConfig(label="l", base_url="http://a:1/v1/", model="m").chat_url,
            "http://a:1/v1/chat/completions",
        )

    def test_empty_base_url_or_model_is_rejected(self):
        with self.assertRaises(ValueError):
            EndpointConfig(label="l", base_url="", model="m")
        with self.assertRaises(ValueError):
            EndpointConfig(label="l", base_url="http://a/v1", model="")

    def test_no_fleet_node_is_hardcoded_anywhere(self):
        """The harness must be able to point at any endpoint, named or not."""
        package = "\n".join(
            p.read_text() for p in (REPO_ROOT / "realtask").glob("*.py")
        )
        entrypoint = (REPO_ROOT / "realtime_bench.py").read_text()
        for node in ("OptiPlex", "optiplex", "Lenovo", "lenovo", "Destroyer", "destroyer",
                     "100.64.43.123", "x1-370", "localhost:1234", "localhost:1235"):
            self.assertNotIn(node, package, "package hardcodes {!r}".format(node))
            self.assertNotIn(node, entrypoint, "entrypoint hardcodes {!r}".format(node))


if __name__ == "__main__":
    unittest.main()


class StreamingErrorIsNotEmptyOutputTests(unittest.TestCase):
    """An in-band stream error must not be recorded as "the model said nothing".

    Found by running the corpus against a real 30B model, not by reading. A
    fixture whose source is larger than the model's context window came back as
    ``EMPTY_OUTPUT``: 0.5s wall, no ``finish_reason``, no token usage, zero bytes
    of content. That reads as a model that produced nothing.

    It was not. The endpoint had answered **HTTP 200** and then reported the
    refusal in-band:

        event: error
        data: {"error":{"message":"... exceeds the available context size ..."}}

    The streaming reader skipped the ``event:`` line, parsed the ``data:`` line,
    found no ``choices``, and continued -- discarding the error. The attempt was
    then filed as ``EMPTY_OUTPUT``, which is a false record. The difference
    matters: "the model returned nothing" is a statement about the model, and the
    truth was a statement about the request. An operator reading the evidence
    would go looking for a model problem instead of a context setting.
    """

    ERROR_FRAME = (
        'event: error\n'
        'data: {"error":{"message":"request (28041 tokens) exceeds the available '
        'context size (16384 tokens)"}}\n\n'
    )

    NORMAL_FRAMES = (
        'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        'data: [DONE]\n\n'
    )

    def _run_stream(self, body: str):
        """Drive ``_complete_stream`` over a canned SSE body."""
        import io

        import realtask.adapter as adapter_module
        from realtask.adapter import ChatRequest, EndpointConfig, OpenAIChatAdapter

        class _FakeResponse(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        adapter = OpenAIChatAdapter(
            EndpointConfig(
                label="test", base_url="http://127.0.0.1:1/v1", model="m"
            )
        )
        payload = {
            "model": "m", "temperature": 0, "max_tokens": 16,
            "stream": True, "messages": [{"role": "user", "content": "x"}],
        }
        request = ChatRequest(role="single", system="s", user="u")

        original = adapter_module.urllib.request.urlopen
        adapter_module.urllib.request.urlopen = (
            lambda _req, timeout=None: _FakeResponse(body.encode("utf-8"))
        )
        try:
            return adapter._complete_stream(request, payload, "sha", 0.0, 0.0)
        finally:
            adapter_module.urllib.request.urlopen = original

    def test_sse_error_frame_is_raised_not_swallowed(self):
        from realtask.adapter import _EndpointRejected

        with self.assertRaises(_EndpointRejected) as caught:
            self._run_stream(self.ERROR_FRAME)
        self.assertIn(
            "exceeds the available context size", str(caught.exception)
        )

    def test_bare_json_error_frame_is_raised_too(self):
        from realtask.adapter import _EndpointRejected

        with self.assertRaises(_EndpointRejected):
            self._run_stream(
                '{"error":{"message":"exceeds the available context size"}}\n'
            )

    def test_a_normal_stream_still_works(self):
        response = self._run_stream(self.NORMAL_FRAMES)
        self.assertEqual(response.content, "hello")
        self.assertEqual(response.finish_reason, "stop")

    def test_an_event_frame_without_data_is_not_mistaken_for_content(self):
        """The ``event:`` line alone must not become the model's answer."""
        from realtask.adapter import _EndpointRejected

        with self.assertRaises(_EndpointRejected):
            self._run_stream(
                'event: error\ndata: {"error":{"message":"boom"}}\n\n'
            )
