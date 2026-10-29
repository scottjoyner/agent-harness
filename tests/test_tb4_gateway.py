import json
import unittest
from tb4.gateway import tune_body, make_handler


class GatewayTests(unittest.TestCase):
    def test_reasoning_off_does_not_change_tool_calls(self):
        body = {"model": "joyner-toolcall", "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "bash"}}],
                "max_tokens": 8192, "chat_template_kwargs": {"mode": "fast"}, "stream": True}
        tuned = json.loads(tune_body(json.dumps(body).encode(),
                                     disable_thinking=True, max_output_tokens=2048))
        self.assertEqual(tuned["tools"], body["tools"])
        self.assertTrue(tuned["stream"])
        self.assertFalse(tuned["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(tuned["chat_template_kwargs"]["mode"], "fast")
        self.assertEqual(tuned["max_tokens"], 2048)
        self.assertEqual(body["max_tokens"], 8192)

    def test_noop_mode_and_reject_invalid_input(self):
        body = b'{"messages":[],"max_tokens":256}'
        self.assertEqual(json.loads(tune_body(body, disable_thinking=False,
                                             max_output_tokens=2048)), json.loads(body))
        for bad in [b'[]', b'{"chat_template_kwargs":7}', b'not-json']:
            with self.assertRaises(ValueError):
                tune_body(bad, disable_thinking=True, max_output_tokens=None)

    def test_gateway_requires_auth_and_fixed_endpoint(self):
        with self.assertRaises(ValueError):
            make_handler('http://127.0.0.1:1234/v1', '', disable_thinking=True)
        with self.assertRaises(ValueError):
            make_handler('http://127.0.0.1:1234/other', 'key', disable_thinking=True)


if __name__ == '__main__':
    unittest.main()
