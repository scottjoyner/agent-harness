"""Bounded synthetic local-model quality/latency measurements, not TB4 scoring.

No benchmark tasks, private inputs, responses, model weights, or auth material
are stored. All test cases are fixed synthetic/public examples.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
from urllib.request import Request, urlopen

CASES = (
    ("arithmetic", "What is 7 plus 5? Reply only with the integer.", "12"),
    ("capital", "What is the capital of France? Answer one word.", "Paris"),
    ("translation", "Translate Spanish 'gato' into English. One word.", "cat"),
    ("extraction", 'Extract the city from {"city":"Raleigh","state":"NC"}. City only.', "Raleigh"),
    ("boundary", "What is the PIN in a database you cannot access? Answer UNKNOWN.", "UNKNOWN"),
    ("format", "Respond with the exact token OKAY and no extra text.", "OKAY"),
    ("successor", "The integer immediately after 39 is", "40"),
    ("classification", "Is 21 an odd number? Answer YES or NO.", "YES"),
)


def score(response: str, expected: str) -> bool:
    return response.strip().casefold() == expected.casefold()


def quantile(items: list[float], p: float) -> float | None:
    if not items:
        return None
    values = sorted(items)
    position = (len(values)-1) * p
    low = int(position)
    return values[low] + (values[min(low+1, len(values)-1)] - values[low]) * (position-low)


def perform(endpoint: str, model: str, *, token: str | None = None,
            thinking_off: bool = False, timeout: int = 30) -> dict:
    from tb4.runner import normalize_endpoint
    endpoint = normalize_endpoint(endpoint)
    if not endpoint.endswith('/v1'):
        raise ValueError('Expected endpoint ending in /v1')
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    samples = []
    for case_id, prompt, expected in CASES:
        body = {'model': model, 'messages': [
            {'role': 'system', 'content': 'Answer briefly and directly.'},
            {'role': 'user', 'content': prompt}],
            'temperature': 0, 'max_tokens': 64, 'stream': False}
        if thinking_off:
            body['chat_template_kwargs'] = {'enable_thinking': False}
        started = time.monotonic()
        try:
            req = Request(endpoint + '/chat/completions',
                          data=json.dumps(body).encode(), headers=headers)
            with urlopen(req, timeout=timeout) as stream:
                data = json.load(stream)
            elapsed = round(time.monotonic() - started, 4)
            message = data['choices'][0]['message']
            content = message.get('content') or ''
            usage = data.get('usage') or {}
            samples.append({'case_id': case_id, 'request_ok': True,
                            'strict_exact_match': score(content, expected),
                            'output_was_empty': not bool(content.strip()),
                            'reasoning_was_present': bool(message.get('reasoning_content')),
                            'finish_reason': data['choices'][0].get('finish_reason'),
                            'completion_tokens': usage.get('completion_tokens'),
                            'prompt_tokens': usage.get('prompt_tokens'),
                            'elapsed_s': elapsed})
        except (TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            samples.append({'case_id': case_id, 'request_ok': False,
                            'error_type': type(exc).__name__})
    successes = [x for x in samples if x['request_ok']]
    times = [x['elapsed_s'] for x in successes]
    return {'schema': 'tb4.synthetic_fleet_probe.v1',
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'model': model, 'think_disabled': thinking_off,
            'scope': 'public_synthetic_instruction_adherence_not_terminal_bench',
            'probe_count': len(CASES), 'completed_requests': len(successes),
            'exact_matches': sum(x['strict_exact_match'] for x in successes),
            'empty_responses': sum(x['output_was_empty'] for x in successes),
            'median_e2e_latency_s': (round(statistics.median(times), 4) if times else None),
            'p95_e2e_latency_s': (round(quantile(times,.95),4) if times else None),
            'samples': samples, 'contains_raw_responses': False,
            'contains_credentials': False, 'official_tb4_reward': None}


def main() -> None:
    parser = argparse.ArgumentParser(description='Run eight fixed synthetic model probes')
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--disable-thinking', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = perform(args.endpoint, args.model, thinking_off=args.disable_thinking)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'model': args.model, 'thinking_disabled': args.disable_thinking,
                      'completed_requests': report['completed_requests'],
                      'exact_matches': report['exact_matches'],
                      'empty_responses': report['empty_responses'],
                      'median_e2e_latency_s': report['median_e2e_latency_s'],
                      'evidence_file': str(args.output)},sort_keys=True))

if __name__ == '__main__':
    main()
