import io
import json
import os
import unittest
from unittest.mock import patch
from tb4.capabilities import probe_tool_call


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class CapabilitiesTests(unittest.TestCase):
    @patch.dict(os.environ, {"TB4_TEST_KEY": "local-no-auth"})
    def test_positive_and_disabled_thinking(self):
        response = {"choices": [{"finish_reason": "tool_calls", "message": {
            "content": "", "tool_calls": [{"type": "function", "function": {
                "name": "tb4_probe", "arguments": '{"label":"ready"}'}}]}}]}
        def fake_open(request, timeout):
            payload = json.loads(request.data)
            self.assertIs(payload["chat_template_kwargs"]["enable_thinking"], False)
            self.assertEqual(payload["tool_choice"], "required")
            return Response(json.dumps(response).encode())
        with patch("tb4.capabilities.urlopen", side_effect=fake_open):
            result = probe_tool_call("http://xwing:1234/v1", "openai/model", "TB4_TEST_KEY",
                                     disable_thinking=True)
        self.assertTrue(result["native_tool_call"])
        self.assertEqual(result["calls_returned"], 1)

    @patch.dict(os.environ, {"TB4_TEST_KEY": "local-no-auth"})
    def test_malformed_arguments_do_not_count(self):
        response = {"choices": [{"message": {"tool_calls": [
            {"type": "function", "function": {"name": "tb4_probe",
                "arguments": "not-json"}}]}}]}
        with patch("tb4.capabilities.urlopen", return_value=Response(json.dumps(response).encode())):
            result = probe_tool_call("http://xwing:1234/v1", "openai/model", "TB4_TEST_KEY")
        self.assertFalse(result["native_tool_call"])

    @patch.dict(os.environ, {"TB4_TEST_KEY": "local-no-auth"})
    def test_text_only_does_not_count_as_tool(self):
        response = {"choices": [{"finish_reason": "stop", "message": {"content": "Call tb4_probe"}}]}
        with patch("tb4.capabilities.urlopen", return_value=Response(json.dumps(response).encode())):
            result = probe_tool_call("http://xwing:1234/v1", "openai/model", "TB4_TEST_KEY")
        self.assertFalse(result["native_tool_call"])


if __name__ == "__main__":
    unittest.main()
