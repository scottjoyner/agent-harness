import json
from pathlib import Path
import tempfile
import unittest
from tb4.report import report_job


class ReportTests(unittest.TestCase):
    def test_official_verifier_only_and_trace_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'result.json').write_text(json.dumps({'id': 'job-1',
                'stats': {'n_completed_trials': 2, 'n_errored_trials': 0,
                          'evals': {'official': {'metrics': [{'mean': .5}]}}}}))
            for name, reward in [('task-a', 0.0), ('task-b', 1.0)]:
                path = root / name
                (path / 'agent').mkdir(parents=True)
                (path / 'result.json').write_text(json.dumps({
                    'task_name': name, 'trial_name': name,
                    'verifier_result': {'rewards': {'reward': reward}},
                    'agent_result': {'n_input_tokens': 123, 'n_output_tokens': 45},
                    'message': 'PASS ALL TESTS'}))
            steps = [
                {'source': 'agent',
                 'tool_calls': [{'function_name': 'read', 'arguments': {'path': '/tmp'}}],
                 'observation': {'results': [
                     {'content': 'Current model does not support images.',
                      'extra': {'is_error': True, 'tool_name': 'read'}}]}},
                {'source': 'agent',
                 'tool_calls': [{'function_name': 'read', 'arguments': {'path': '/tmp'}}]},
            ]
            (root / 'task-a' / 'agent' / 'trajectory.json').write_text(
                json.dumps({'steps': steps}))
            (root / 'task-a' / 'agent' / 'pi.txt').write_text('\n'.join(json.dumps(x) for x in [
                {'type':'turn_end'},
                {'type':'message_end','message':{'role':'assistant','stopReason':'error',
                    'errorMessage':'400: request (9000 tokens) exceeds the available context size (8192 tokens), exceed_context_size_error'}},
                {'type':'message_end','message':{'role':'assistant','stopReason':'error',
                    'errorMessage':'400 Invalid JSON payload'}},
            ]))
            data = report_job(root)
            self.assertEqual(data['official_completed'], 2)
            self.assertEqual(data['passes_from_official_verifier'], 1)
            self.assertEqual(data['trials'][0]['reward'], 0.0)
            diag = data['trials'][0]['diagnostics']
            self.assertTrue(diag['reported_image_modality_unsupported'])
            self.assertEqual(diag['consecutive_duplicate_calls'], 1)
            self.assertEqual(diag['tool_errors']['read'], 1)
            self.assertEqual(diag['inference_errors']['upstream_context_overflow'], 1)
            self.assertEqual(diag['inference_errors']['gateway_rejected_request'], 1)
            self.assertEqual(diag['model_turns'], 1)


if __name__ == '__main__':
    unittest.main()
