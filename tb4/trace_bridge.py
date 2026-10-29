"""Opt-in, bounded metadata-only Pi/Harbor -> Trace Viewer event exporter.

No task prompts, tool arguments, outputs, raw trial IDs, credentials or remote
endpoints appear in the event file. Never treats model completion as TB4 pass.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

SCHEMA = 'tb4.telemetry.v1'
EVENT_MAX_BYTES = 2048
LABEL = re.compile(r'^[a-zA-Z0-9_.:/@+ -]{1,90}$')


def name(value: object, *, fallback: str = 'unknown') -> str:
    if not isinstance(value, str) or not LABEL.fullmatch(value):
        return fallback
    return value


def count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 100_000_000):
        return 0
    return value


def utc_ms(value: object) -> str | None:
    if not isinstance(value, (int, float)) or not 946684800000 <= value <= 4102444800000:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def digest(*parts: object) -> str:
    return hashlib.sha256('\x00'.join(map(str, parts)).encode()).hexdigest()


def event_from_message(message: dict, *, session_id: str, ordinal: int, host: str) -> dict | None:
    if message.get('role') != 'assistant':
        return None
    when = utc_ms(message.get('timestamp'))
    usage = message.get('usage') or {}
    if not when or not isinstance(usage, dict):
        return None
    source_total = count(usage.get('totalTokens'))
    input_tokens = count(usage.get('input'))
    output_tokens = count(usage.get('output'))
    cache_read = count(usage.get('cacheRead'))
    cache_write = count(usage.get('cacheWrite'))
    if not (source_total or input_tokens or output_tokens or cache_read or cache_write):
        return None
    return {
        'schema': SCHEMA, 'event_type': 'model_turn',
        'event_id': digest('pi', session_id, ordinal),
        'session_id': session_id, 'source': 'terminal-bench',
        'host': name(host), 'model': name(message.get('model')),
        'ts': when, 'input_tokens': input_tokens, 'output_tokens': output_tokens,
        'total_tokens': source_total or input_tokens + output_tokens + cache_read + cache_write,
        'reasoning_tokens': count(usage.get('reasoning')),
        'cache_read': cache_read, 'cache_write': cache_write,
        'status': 'error' if message.get('stopReason') == 'error' else 'completed',
    }


def verifier_event(result: dict, *, session_id: str, host: str, ts: str | None = None) -> dict | None:
    verifier = result.get('verifier_result') or {}
    reward = (verifier.get('rewards') or {}).get('reward')
    if isinstance(reward, bool) or not isinstance(reward, (int, float)) or not 0 <= reward <= 1 or not ts:
        return None
    return {'schema': SCHEMA, 'event_type': 'trial_verified',
            'event_id': digest('official-verifier', session_id),
            'session_id': session_id, 'source': 'terminal-bench',
            'host': name(host), 'ts': ts, 'official_reward': float(reward),
            'status': 'passed' if reward == 1 else 'failed'}


class Reader:
    """One running job; incremental complete-line reads, deterministic per-file ordinals."""
    def __init__(self, job_dir: Path, host: str):
        self.job_dir = job_dir
        if name(host) != host or host == 'unknown':
            raise ValueError('host must be a vetted, nonempty machine label')
        self.host = host
        self.state: dict[Path, tuple[int, int]] = {}
        self.verifiers: set[str] = set()

    def export_to(self, path: Path) -> int:
        """Poll and journal as one retryable unit.

        Reader cursors must never acknowledge records that failed to reach the
        private journal. Re-reading after a failed append reproduces the same
        deterministic event IDs; the downstream importer deduplicates replay.
        """
        previous_state = dict(self.state)
        previous_verifiers = set(self.verifiers)
        try:
            records = self.poll()
            return append_private(path, records)
        except Exception:
            self.state = previous_state
            self.verifiers = previous_verifiers
            raise

    def poll(self) -> list[dict]:
        output = []
        # Only one task directory per Pi trial, but job may contain many trials.
        jobs = self.job_dir / 'harbor' / self.job_dir.name
        for file in sorted(jobs.glob('*/agent/pi.txt')):
            if not file.is_file() or file.is_symlink():
                continue
            session_id = digest('job-session', self.job_dir.name, file.parent.parent.name)
            offset, ordinal = self.state.get(file, (0, 0))
            if file.stat().st_size < offset:
                raise RuntimeError('Pi log truncated during active run')
            with file.open('rb') as stream:
                stream.seek(offset)
                while True:
                    start = stream.tell()
                    line = stream.readline(EVENT_MAX_BYTES * 1024 + 1)
                    if not line or not line.endswith(b'\n'):
                        break  # do not advance over partially written events
                    offset = stream.tell()
                    if len(line) > EVENT_MAX_BYTES * 1024:
                        continue  # discard oversized logs instead of decoding them
                    try:
                        obj = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if obj.get('type') != 'message_end':
                        continue
                    message = obj.get('message')
                    if not isinstance(message, dict) or message.get('role') != 'assistant':
                        continue
                    ordinal += 1
                    record = event_from_message(message, session_id=session_id,
                                                ordinal=ordinal, host=self.host)
                    if record:
                        output.append(record)
                self.state[file] = (offset, ordinal)
            result_file = file.parent.parent / 'result.json'
            if result_file.is_file() and session_id not in self.verifiers:
                try:
                    result = json.loads(result_file.read_text())
                    record = verifier_event(result, session_id=session_id, host=self.host,
                                            ts=utc_ms(int(result_file.stat().st_mtime * 1000)))
                    if record:
                        self.verifiers.add(session_id)
                        output.append(record)
                except (ValueError, OSError):
                    pass
        return output


def append_private(path: Path, events: list[dict]) -> int:
    """Append strictly bounded JSONL under a private directory with NOFOLLOW."""
    if not events:
        return 0
    if not path.is_absolute():
        raise ValueError('telemetry output must be absolute')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise ValueError('telemetry output directory must be mode 0700')
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
            raise ValueError('unsafe telemetry output file')
        if st.st_size >= 32 * 1024 * 1024:
            raise ValueError('telemetry file exceeds 32MiB; rotate manually')
        buf = b''
        for event in events:
            if event.get('schema') != SCHEMA:
                raise ValueError('invalid event schema')
            line = (json.dumps(event, separators=(',', ':'), sort_keys=True) + '\n').encode()
            if len(line) > EVENT_MAX_BYTES:
                raise ValueError('oversized sanitized event')
            buf += line
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            original_size = os.fstat(fd).st_size
            if original_size + len(buf) > 32 * 1024 * 1024:
                raise ValueError('telemetry output capacity exceeded')
            # An unbuffered write may be short even without an exception.
            # Preserve complete JSONL records (or roll back the whole batch),
            # so a partial disk-full write cannot corrupt the next scan.
            written = 0
            try:
                while written < len(buf):
                    n = os.write(fd, buf[written:])
                    if n <= 0:
                        raise OSError('telemetry append made no progress')
                    written += n
                os.fsync(fd)
            except BaseException:
                os.ftruncate(fd, original_size)
                os.fsync(fd)
                raise
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
        return len(events)
    finally:
        os.close(fd)
