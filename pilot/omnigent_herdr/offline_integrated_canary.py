#!/usr/bin/env python3
"""One-process synthetic Git + denied broker canary in strict Bubblewrap.

This deliberately does not start an LLM agent, run a real user repository,
contact an API, authenticate, or grant any model dispatch authority.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
from pathlib import Path

from egress_broker_probe import decide

MAX_MESSAGE = 2048
GUEST = r"""
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

challenge, secret, sentinel = sys.argv[1:4]
def git(*args):
    return subprocess.run(['/usr/bin/git', '-C', '/workspace', *args],
        check=True, capture_output=True, text=True, timeout=5)

# Synthetic fixture only: commit from inside the restricted namespace.
Path('/workspace/feature.txt').write_text('offline synthetic canary\n', encoding='utf-8')
git('add', '--', 'feature.txt')
git('-c', 'core.hooksPath=/dev/null',
    '-c', 'user.name=Fixture',
    '-c', 'user.email=fixture@invalid.test',
    'commit', '-qm', 'Synthetic isolated canary')

request_list = [
    {'schema': 1, 'op': 'probe', 'challenge': challenge},
    {'schema': 1, 'op': 'dispatch', 'provider': 'synthetic'},
    {'schema': 1, 'op': 'probe', 'challenge': 'not-the-challenge'},
    {'schema': 1, 'op': 'shell', 'command': 'never-execute'},
]
statuses = []
for request in request_list:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect('/run/fleet-broker/probe.sock')
        client.sendall((json.dumps(request) + '\n').encode())
        client.shutdown(socket.SHUT_WR)
        response = client.recv(2048)
    status = json.loads(response)
    statuses.append((status.get('status'), status.get('dispatch_authorized')))

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ipv4:
    ipv4.settimeout(1)
    network_rc = ipv4.connect_ex(('192.0.2.1', 443))
status = dict(line.split(':', 1) for line in
              Path('/proc/self/status').read_text().splitlines() if ':' in line)
visible_pids = [p for p in Path('/proc').iterdir() if p.name.isdecimal()]
result = {
    'broker_denial': (
        statuses == [('synthetic_probe_only', False)]
        + [('denied', False)] * 3),
    'git_commit': (
        git('show', '--format=', '--name-only', 'HEAD').stdout.strip()
        == 'feature.txt' and not git('status', '--porcelain').stdout.strip()),
    'head': git('rev-parse', 'HEAD').stdout.strip(),
    'network_denied': network_rc in
        (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN),
    'host_secret_unreadable': not Path(secret).exists(),
    'host_sentinel_unreachable': not Path(sentinel).exists(),
    'pid_isolated': len(visible_pids) <= 5 and os.getpid() <= 5,
    'caps_dropped': int(status.get('CapEff', '1').strip(), 16) == 0,
    'no_new_privs': status.get('NoNewPrivs', '').strip() == '1',
}
print(json.dumps(result, sort_keys=True))
"""

def sandbox_command(binary: str, clone: Path, sockdir: Path,
                    secret: Path, sentinel: Path, challenge: str) -> list[str]:
    return [
        binary, '--unshare-user', '--unshare-pid', '--unshare-net',
        '--unshare-ipc', '--unshare-uts', '--die-with-parent',
        '--new-session', '--clearenv', '--cap-drop', 'ALL',
        '--dir', '/usr', '--ro-bind', '/usr', '/usr',
        '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
        '--symlink', 'usr/lib64', '/lib64',
        '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp',
        '--dir', '/workspace', '--bind', str(clone), '/workspace',
        '--dir', '/run', '--dir', '/run/fleet-broker',
        '--ro-bind', str(sockdir), '/run/fleet-broker',
        '--setenv', 'HOME', '/workspace',
        '--setenv', 'PATH', '/usr/bin:/bin',
        '--setenv', 'LANG', 'C',
        '--setenv', 'GIT_CONFIG_NOSYSTEM', '1',
        '--setenv', 'GIT_CONFIG_GLOBAL', '/dev/null',
        '--setenv', 'GIT_TERMINAL_PROMPT', '0',
        '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
        '--', '/usr/bin/python3', '-c', GUEST, challenge, str(secret),
        str(sentinel),
    ]


def inspect(*, which=shutil.which, runner=subprocess.run) -> dict:
    result = {
        'schema': 1, 'observed_host': socket.gethostname(),
        'mode': 'deny_only_integrated_synthetic_canary',
        'integrated_synthetic_passed': False,
        'broker_denial_verified': False,
        'isolated_git_verified': False,
        'host_state_unchanged': False,
        'direct_network_denied': False,
        'sandbox_capabilities_dropped': False,
        'sandbox_privilege_escalation_denied': False,
        'provider_requests_forwarded': 0,
        'remote_push_attempted': False,
        'used_real_repository': False,
        'agent_dispatch_authorized': False,
        'errors': [],
    }
    bwrap, git = which('bwrap'), which('git')
    if not bwrap or not git:
        result['errors'].append('missing bwrap or git')
        return result

    def call(argv: list[str], *, cwd: Path | None = None,
             env: dict | None = None) -> str:
        out = runner(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                     capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip()

    cache = Path.home() / '.cache'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='og-integrated-', dir=cache) as temp:
        root = Path(temp)
        for name in ('seed', 'transport', 'protected'):
            (root / name).mkdir(mode=0o700)
        seed, clone = root / 'seed', root / 'clone'
        transport, protected = root / 'transport', root / 'protected'
        secret, sentinel = protected / 'secret', protected / 'sentinel'
        secret.write_text('HOST-SECRET-DO-NOT-DISCLOSE', encoding='utf-8')
        sentinel.write_text('untouched', encoding='utf-8')
        env = {
            'HOME': str(root), 'PATH': '/usr/bin:/bin',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_TERMINAL_PROMPT': '0',
        }
        challenge = secrets.token_hex(16)
        socket_path = transport / 'probe.sock'
        ready = threading.Event()
        observed = {'messages': 0, 'peer_uid_ok': True}

        def deny_only_server() -> None:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                    server.bind(str(socket_path))
                    os.chmod(socket_path, 0o600)
                    server.listen(4)
                    server.settimeout(9)
                    ready.set()
                    for _ in range(4):
                        conn, _ = server.accept()
                        with conn:
                            conn.settimeout(3)
                            cred = conn.getsockopt(
                                socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                            _, uid, _ = struct.unpack('3i', cred)
                            observed['peer_uid_ok'] &= uid == os.getuid()
                            data = conn.recv(MAX_MESSAGE + 1)
                            try:
                                req = json.loads(data) if len(data) <= MAX_MESSAGE else None
                            except (ValueError, UnicodeDecodeError):
                                req = None
                            response = decide(req, challenge=challenge,
                                              peer_uid=uid,
                                              expected_uid=os.getuid())
                            conn.sendall((json.dumps(response) + '\n').encode())
                            observed['messages'] += 1
            except Exception:
                observed['error'] = 'broker fixture error'
                ready.set()

        try:
            call([git, 'init', '-q', '-b', 'main', str(seed)], env=env)
            (seed / 'README.md').write_text('synthetic seed\n', encoding='utf-8')
            call([git, '-C', str(seed), 'add', '--', 'README.md'], env=env)
            call([git, '-C', str(seed),
                  '-c', 'core.hooksPath=/dev/null',
                  '-c', 'user.name=Fixture',
                  '-c', 'user.email=fixture@invalid.test',
                  'commit', '-qm', 'Synthetic seed'], env=env)
            original = call([git, '-C', str(seed), 'rev-parse', 'HEAD'], env=env)
            call([git, 'clone', '--quiet', '--no-hardlinks', '--local',
                  str(seed), str(clone)], env=env)
            if call([git, '-C', str(clone), 'rev-parse', 'HEAD'], env=env) != original:
                result['errors'].append('clone HEAD mismatch')
                return result
            thread = threading.Thread(target=deny_only_server, daemon=True)
            thread.start()
            if not ready.wait(4) or observed.get('error'):
                result['errors'].append('broker fixture not ready')
                return result
            completed = runner(
                sandbox_command(bwrap, clone, transport, secret, sentinel,
                                challenge),
                stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=14)
            thread.join(timeout=9)
            try:
                guest = json.loads(completed.stdout)
            except ValueError:
                guest = {}
            checks = (
                'network_denied', 'host_secret_unreadable',
                'host_sentinel_unreachable', 'pid_isolated',
                'caps_dropped', 'no_new_privs',
            )
            result['broker_denial_verified'] = (
                guest.get('broker_denial') is True
                and observed['messages'] == 4
                and observed['peer_uid_ok'] is True
                and 'error' not in observed)
            result['isolated_git_verified'] = (
                guest.get('git_commit') is True
                and call([git, '-C', str(clone), 'rev-parse', 'HEAD'], env=env)
                    == guest.get('head')
                and call([git, '-C', str(clone), 'show', '--format=',
                          '--name-only', 'HEAD'], env=env) == 'feature.txt'
                and call([git, '-C', str(clone), 'status', '--porcelain'],
                         env=env) == ''
                and guest.get('head') != original)
            result['host_state_unchanged'] = (
                call([git, '-C', str(seed), 'rev-parse', 'HEAD'],
                     env=env) == original
                and call([git, '-C', str(seed), 'status', '--porcelain'],
                         env=env) == ''
                and secret.read_text(encoding='utf-8')
                    == 'HOST-SECRET-DO-NOT-DISCLOSE'
                and sentinel.read_text(encoding='utf-8') == 'untouched')
            result['direct_network_denied'] = guest.get('network_denied') is True
            result['sandbox_capabilities_dropped'] = (
                guest.get('caps_dropped') is True)
            result['sandbox_privilege_escalation_denied'] = (
                guest.get('no_new_privs') is True)
            result['integrated_synthetic_passed'] = (
                completed.returncode == 0
                and all(guest.get(check) is True for check in checks)
                and all(result[key] is True for key in (
                    'broker_denial_verified', 'isolated_git_verified',
                    'host_state_unchanged', 'direct_network_denied',
                    'sandbox_capabilities_dropped',
                    'sandbox_privilege_escalation_denied')))
            if not result['integrated_synthetic_passed']:
                result['errors'].append('guest witness or host postcheck denied')
        except (OSError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired):
            result['errors'].append('fixture preparation, process or postcheck failed')
    return result


def exit_status(report: dict) -> int:
    """Refuse successful CLI status unless physical proof passed."""
    return 0 if report.get('integrated_synthetic_passed') is True else 2


if __name__ == '__main__':
    report = inspect()
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(exit_status(report))
