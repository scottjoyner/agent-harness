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


class ContextBudgetTests(unittest.TestCase):
    def sample(self):
        messages = [
            {'role': 'system', 'content': 'trusted agent guidance'},
            {'role': 'user', 'content': 'Complete the original objective'},
        ]
        for i in range(16):
            messages += [
                {'role': 'assistant', 'content': 'checking source',
                 'tool_calls': [{'id': f'call_{i}', 'type': 'function',
                                 'function': {'name': 'bash', 'arguments': '{"command":"ls"}'}}]},
                {'role': 'tool', 'tool_call_id': f'call_{i}', 'content': 'large-output-' + 'x' * 3100},
            ]
        return {'model': 'test', 'messages': messages,
                'tools': [{'type': 'function', 'function': {'name': 'bash', 'parameters': {}}}],
                'max_tokens': 1024, 'stream': True}

    def test_context_compaction_preserves_instructions_tools_and_latest_messages(self):
        from tb4.gateway import _estimate_prompt_tokens
        body = self.sample()
        original = json.loads(json.dumps(body))
        tuned = json.loads(tune_body(json.dumps(body).encode(), disable_thinking=True,
                                     max_output_tokens=1024, context_window=8192))
        self.assertEqual(tuned['messages'][:2], original['messages'][:2])
        self.assertEqual([(m['role'],m.get('tool_call_id'),m.get('tool_calls')) for m in tuned['messages'][-2:]],
                         [(m['role'],m.get('tool_call_id'),m.get('tool_calls')) for m in original['messages'][-2:]])
        self.assertEqual(tuned['tools'], original['tools'])
        self.assertTrue(tuned['stream'])
        self.assertLessEqual(_estimate_prompt_tokens(tuned), 8192-1024-640)
        self.assertLess(len(json.dumps(tuned)), len(json.dumps(original)))
        active_calls = {c['id'] for m in tuned['messages'] for c in m.get('tool_calls', [])}
        for m in tuned['messages']:
            if m['role'] == 'tool':
                self.assertIn(m['tool_call_id'], active_calls)
        self.assertEqual(body, original)

    def test_no_history_change_when_within_budget(self):
        body = {'messages':[{'role':'system','content':'rules'}, {'role':'user','content':'task'}],
                'max_tokens': 200}
        tuned=json.loads(tune_body(json.dumps(body).encode(), disable_thinking=False,
                                   max_output_tokens=1024,context_window=8192))
        self.assertEqual(tuned, body)

    def test_never_truncate_initial_task_even_if_oversized(self):
        body = {'messages':[{'role':'system','content':'rules'},
                            {'role':'user','content':'X'*25000}], 'max_tokens':1024}
        with self.assertRaisesRegex(ValueError, 'Immutable instructions'):
            tune_body(json.dumps(body).encode(), disable_thinking=True,
                      max_output_tokens=1024, context_window=8192)

    def test_telemetry_without_raw_prompts(self):
        from threading import Lock
        stats={'lock':Lock(),'requests':0,'tool_outputs_compacted':0,
               'old_turns_dropped':0,'max_estimated_prompt_tokens':0}
        body=self.sample()
        tune_body(json.dumps(body).encode(), disable_thinking=True,
                  max_output_tokens=1024, context_window=8192,telemetry=stats)
        self.assertEqual(stats['requests'], 1)
        self.assertGreater(stats['tool_outputs_compacted'], 0)
        self.assertGreater(stats['max_estimated_prompt_tokens'], 0)
        self.assertNotIn('Complete the original objective',str(stats))

    def test_short_history_with_huge_latest_tool_result_is_bounded(self):
        body={'messages':[
            {'role':'system','content':'policy must stay'},
            {'role':'user','content':'the full task must stay'},
            {'role':'assistant','tool_calls':[{'id':'c1','type':'function',
                'function':{'name':'read','arguments':'{"path":"manual"}'}}]},
            {'role':'tool','tool_call_id':'c1','content':'A'*35000},
        ],'tools':[{'type':'function','function':{'name':'read'}}]}
        tuned=json.loads(tune_body(json.dumps(body).encode(),disable_thinking=True,
                                   max_output_tokens=512,context_window=8192))
        self.assertEqual(tuned['messages'][0]['content'],'policy must stay')
        self.assertEqual(tuned['messages'][1]['content'],'the full task must stay')
        self.assertEqual(tuned['messages'][3]['tool_call_id'],'c1')
        self.assertIn('abbreviated',tuned['messages'][3]['content'])


class GatewayAuthIntegrationTests(unittest.TestCase):
    def test_gateway_uses_ephemeral_token_and_never_leaks_it_upstream(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        received=[]
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                received.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"test"}]}')
        upstream=ThreadingHTTPServer(('127.0.0.1',0), Upstream)
        gateway=ThreadingHTTPServer(('127.0.0.1',0), make_handler(
            f'http://127.0.0.1:{upstream.server_port}/v1','random-gateway-secret',
            disable_thinking=True,forward_auth=True,upstream_key='upstream-only-key'))
        workers=[Thread(target=s.serve_forever,daemon=True) for s in (upstream,gateway)]
        for t in workers:t.start()
        try:
            url=f'http://127.0.0.1:{gateway.server_port}/v1/models'
            with self.assertRaises(HTTPError) as err:
                urlopen(Request(url,headers={'Authorization':'Bearer upstream-only-key'}),timeout=3)
            self.assertEqual(err.exception.code,401)
            self.assertEqual(received,[])
            with urlopen(Request(url,headers={'Authorization':'Bearer random-gateway-secret'}),timeout=3) as result:
                self.assertEqual(json.load(result)['data'][0]['id'],'test')
            self.assertEqual(received,['Bearer upstream-only-key'])
        finally:
            for s in (gateway,upstream):s.shutdown();s.server_close()
            for t in workers:t.join(timeout=3)
