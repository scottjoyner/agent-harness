#!/usr/bin/env python3
"""Verify a minimal-root Linux Bubblewrap sandbox without launching any agent.

The probe deliberately blocks network access. A passing result is NOT a
working cloud-LLM agent configuration or authorization to dispatch.
"""
from __future__ import annotations

import errno
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

PAYLOAD = r"""
import errno
import os
import socket
import sys
from pathlib import Path

outside_secret, outside_sentinel = map(Path, sys.argv[1:])
workspace = Path('/workspace')
(workspace / 'proof').write_text('sandboxed', encoding='utf-8')
print('WORKSPACE_WRITE_OK', flush=True)

try:
    outside_secret.read_text(encoding='utf-8')
except (OSError, PermissionError):
    print('OUTSIDE_READ_DENIED', flush=True)

try:
    outside_sentinel.write_text('tampered', encoding='utf-8')
except (OSError, PermissionError):
    print('OUTSIDE_WRITE_DENIED', flush=True)

sock = socket.socket()
sock.settimeout(1)
try:
    result = sock.connect_ex(('192.0.2.1', 443))
    if result in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN):
        print('NETWORK_DENIED', flush=True)
finally:
    sock.close()

processes = [p for p in Path('/proc').iterdir() if p.name.isdecimal()]
if len(processes) <= 5 and os.getpid() <= 5:
    print('PID_ISOLATED', flush=True)

status = Path('/proc/self/status').read_text(encoding='utf-8').splitlines()
values = dict(line.split(':', 1) for line in status if ':' in line)
if int(values.get('CapEff', '1').strip(), 16) == 0:
    print('CAPABILITIES_DROPPED', flush=True)
if values.get('NoNewPrivs', '').strip() == '1':
    print('NO_NEW_PRIVILEGES', flush=True)
"""
REQUIRED = {
    'WORKSPACE_WRITE_OK', 'OUTSIDE_READ_DENIED', 'OUTSIDE_WRITE_DENIED',
    'NETWORK_DENIED', 'PID_ISOLATED', 'CAPABILITIES_DROPPED',
    'NO_NEW_PRIVILEGES',
}


def inspect(*, which=shutil.which, runner=subprocess.run) -> dict:
    report = {
        'schema': 1,
        'strict_isolation_passed': False,
        'agent_dispatch_authorized': False,
        'network_available_to_agent': False,
        'method': 'minimal-root bwrap user+pid+net+ipc+uts, no host home',
        'errors': [],
    }
    binary = which('bwrap')
    if not binary:
        report['errors'].append('bwrap executable unavailable')
        return report
    cache = Path.home() / '.cache'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='og-strict-sandbox-', dir=cache) as path:
        root = Path(path)
        work, outside = root / 'work', root / 'outside'
        work.mkdir()
        outside.mkdir()
        secret, sentinel = outside / 'private-token', outside / 'sentinel'
        secret.write_text('PRIVATE_SHOULD_BE_UNREADABLE', encoding='utf-8')
        sentinel.write_text('untouched', encoding='utf-8')
        argv = [
            binary, '--unshare-user', '--unshare-pid', '--unshare-net',
            '--unshare-ipc', '--unshare-uts', '--die-with-parent',
            '--new-session', '--clearenv', '--cap-drop', 'ALL',
            '--dir', '/usr', '--ro-bind', '/usr', '/usr',
            '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
            '--symlink', 'usr/lib64', '/lib64',
            '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp',
            '--dir', '/workspace', '--bind', str(work), '/workspace',
            '--setenv', 'HOME', '/workspace',
            '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'LANG', 'C',
            '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
            '--', '/usr/bin/python3', '-c', PAYLOAD, str(secret), str(sentinel),
        ]
        try:
            outcome = runner(argv, stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            report['errors'].append('bubblewrap failed to start or timed out')
            return report
        seen = set(outcome.stdout.splitlines())
        workspace_proof = work / 'proof'
        report['strict_isolation_passed'] = (
            outcome.returncode == 0 and REQUIRED <= seen
            and workspace_proof.is_file()
            and workspace_proof.read_text(encoding='utf-8') == 'sandboxed'
            and sentinel.read_text(encoding='utf-8') == 'untouched'
            and secret.read_text(encoding='utf-8') == 'PRIVATE_SHOULD_BE_UNREADABLE'
        )
        if not report['strict_isolation_passed']:
            report['errors'].append('strict negative evidence missing or host guard changed')
            if outcome.returncode:
                report['errors'].append('bubblewrap returned nonzero')
    return report


if __name__ == '__main__':
    result = inspect()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result['strict_isolation_passed'] else 2)
