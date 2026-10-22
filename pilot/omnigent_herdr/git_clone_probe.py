#!/usr/bin/env python3
"""Disposable Git-clone commit proof inside a network-isolated minimal root.

Uses generated fixture data only; NEVER checks out or edits a user repository.
A clone is intentionally used instead of a linked Git worktree because a
worktree's .git file points to host metadata outside the isolated mount.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

GUEST = r'''
import errno
import json
import socket
import subprocess
from pathlib import Path

work = Path('/workspace')
feature = work / 'feature.txt'
feature.write_text('synthetic offline commit\n', encoding='utf-8')
def git(*args):
    return subprocess.run(
        ['/usr/bin/git', '-C', '/workspace', *args],
        capture_output=True, text=True, timeout=7, check=True)
git('add', '--', 'feature.txt')
git('-c', 'core.hooksPath=/dev/null',
    '-c', 'user.name=Sandbox Fixture',
    '-c', 'user.email=fixture@invalid.test',
    'commit', '-qm', 'Isolated synthetic change')
files = git('show', '--format=', '--name-only', 'HEAD').stdout.splitlines()
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.settimeout(1)
    no_network = s.connect_ex(('192.0.2.1', 443)) in (
        errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN)
print(json.dumps({
    'committed': len(files) == 1 and files[0] == 'feature.txt',
    'network_denied': no_network,
    'head': git('rev-parse', 'HEAD').stdout.strip(),
    'clean': git('status', '--porcelain').stdout.strip() == '',
}))
'''


def inspect(*, which=shutil.which, runner=subprocess.run) -> dict:
    result = {
        "schema": 1, "observed_host": socket.gethostname(),
        "isolated_commit_verified": False,
        "seed_unchanged": False, "clone_clean": False,
        "outside_guard_unchanged": False, "direct_network_denied": False,
        "remote_push_attempted": False, "used_real_repository": False,
        "agent_dispatch_authorized": False, "errors": [],
    }
    bwrap = which('bwrap')
    git = which('git')
    if not bwrap or not git:
        result['errors'].append('missing sandbox or Git executable')
        return result
    cache = Path.home() / '.cache'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='og-git-fixture-', dir=cache) as path:
        root = Path(path)
        seed, clone = root / 'seed', root / 'clone'
        outside = root / 'protected'
        outside.mkdir()
        guard = outside / 'sentinel'
        guard.write_text('untouched', encoding='utf-8')
        git_env = {
            'HOME': str(root), 'PATH': '/usr/bin:/bin',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_TERMINAL_PROMPT': '0',
        }

        def host_git(*args: str, cwd: Path | None = None) -> str:
            done = runner([git, *args], env=git_env, cwd=cwd, text=True,
                          capture_output=True, timeout=12, check=True)
            return done.stdout.strip()

        try:
            host_git('init', '-q', '-b', 'main', str(seed))
            (seed / 'README.md').write_text('Synthetic repository\n', encoding='utf-8')
            host_git('-C', str(seed), 'add', '--', 'README.md')
            host_git('-C', str(seed), '-c', 'user.name=Sandbox Fixture',
                     '-c', 'user.email=fixture@invalid.test',
                     '-c', 'core.hooksPath=/dev/null',
                     'commit', '-qm', 'Synthetic seed')
            base_head = host_git('-C', str(seed), 'rev-parse', 'HEAD')
            host_git('clone', '--quiet', '--no-hardlinks', '--local',
                     str(seed), str(clone))
            before = host_git('-C', str(clone), 'rev-parse', 'HEAD')
            if base_head != before:
                result['errors'].append('unexpected initial clone state')
                return result
            argv = [
                bwrap, '--unshare-user', '--unshare-pid', '--unshare-net',
                '--unshare-ipc', '--unshare-uts', '--die-with-parent',
                '--new-session', '--clearenv', '--cap-drop', 'ALL',
                '--dir', '/usr', '--ro-bind', '/usr', '/usr',
                '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
                '--symlink', 'usr/lib64', '/lib64', '--dev', '/dev',
                '--proc', '/proc', '--tmpfs', '/tmp',
                '--dir', '/workspace', '--bind', str(clone), '/workspace',
                '--setenv', 'HOME', '/workspace',
                '--setenv', 'PATH', '/usr/bin:/bin',
                '--setenv', 'LANG', 'C',
                '--setenv', 'GIT_CONFIG_NOSYSTEM', '1',
                '--setenv', 'GIT_CONFIG_GLOBAL', '/dev/null',
                '--setenv', 'GIT_TERMINAL_PROMPT', '0',
                '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
                '--', '/usr/bin/python3', '-c', GUEST,
            ]
            completed = runner(argv, stdin=subprocess.DEVNULL,
                               text=True, capture_output=True, timeout=17)
            try:
                witness = json.loads(completed.stdout)
            except ValueError:
                witness = {}
            after = host_git('-C', str(clone), 'rev-parse', 'HEAD')
            seed_after = host_git('-C', str(seed), 'rev-parse', 'HEAD')
            status = host_git('-C', str(clone), 'status', '--porcelain')
            changed = host_git('-C', str(clone), 'show',
                               '--format=', '--name-only', 'HEAD')
            result['seed_unchanged'] = seed_after == base_head and (
                host_git('-C', str(seed), 'status', '--porcelain') == '')
            result['outside_guard_unchanged'] = guard.read_text() == 'untouched'
            result['clone_clean'] = status == ''
            result['direct_network_denied'] = witness.get('network_denied') is True
            result['isolated_commit_verified'] = (
                completed.returncode == 0 and witness.get('committed') is True
                and witness.get('clean') is True
                and after != before and witness.get('head') == after
                and changed == 'feature.txt' and result['seed_unchanged']
                and result['outside_guard_unchanged'] and result['clone_clean']
                and result['direct_network_denied'])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            result['errors'].append('fixture setup or sandbox Git operation failed')
        if not result['isolated_commit_verified'] and not result['errors']:
            result['errors'].append('Git isolation or rollback guard proof incomplete')
    # All disposable clone data is removed by TemporaryDirectory on exit.
    return result


def exit_status(report: dict) -> int:
    """Refuse successful CLI status unless physical proof passed."""
    return 0 if report.get('isolated_commit_verified') is True else 2


if __name__ == '__main__':
    report = inspect()
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(exit_status(report))
