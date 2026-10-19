#!/usr/bin/env python3
"""Read-only host audit; does not install, authenticate, or launch anything."""
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

COMMANDS = ('python3', 'git', 'tmux', 'uv', 'herdr', 'omnigent', 'claude', 'codex', 'kilo', 'opencode', 'blink', 'cmd-acp')


def report(which=shutil.which, hostname=platform.node, disk=shutil.disk_usage):
    home = Path.home()
    available = {cmd: which(cmd) for cmd in COMMANDS}
    return {
        'schema': 1,
        'host': hostname(),
        'os': platform.system(),
        'architecture': platform.machine(),
        'disk_free_bytes': disk(home).free,
        'executables': available,
        'all_prerequisites_present': all(available.values()),
        'agent_dispatch_authorized': False,
        'installation_authorized': False,
        'reason': 'Read-only inventory is not runtime, authentication, sandbox, or model-pin proof',
    }


if __name__ == '__main__':
    print(json.dumps(report(), indent=2, sort_keys=True))
