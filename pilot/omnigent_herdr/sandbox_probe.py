#!/usr/bin/env python3
"""Fail-closed physical Linux bwrap isolation test; never launches an agent."""
from __future__ import annotations

import errno
import json
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

PAYLOAD = """import errno, pathlib, socket, sys
work, outside = map(pathlib.Path, sys.argv[1:3])
(work / 'proof').write_text('sandboxed')
print('WORKSPACE_OK')
try:
    (outside / 'sentinel').write_text('modified')
except OSError as exc:
    if exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM):
        print('OUTSIDE_DENIED')
sock = socket.socket()
sock.settimeout(1)
status = sock.connect_ex(('192.0.2.1', 443))
sock.close()
if status in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN):
    print('NETWORK_DENIED')
"""

def inspect(which=shutil.which, runner=subprocess.run) -> dict:
    result = {"host": platform.node(), "sandbox_verified": False,
              "agent_dispatch_authorized": False, "error": None}
    if not which("bwrap"):
        result["error"] = "bwrap missing"
        return result
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="og-sandbox-", dir=cache) as tmp:
        root = Path(tmp)
        work, outside = root / "work", root / "outside"
        work.mkdir()
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("untouched")
        command = [which("bwrap"), "--unshare-user", "--unshare-net",
                   "--die-with-parent", "--ro-bind", "/", "/", "--dev-bind",
                   "/dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
                   "--bind", str(work), str(work), "--", "/usr/bin/python3",
                   "-c", PAYLOAD, str(work), str(outside)]
        try:
            completed = runner(command, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["error"] = type(exc).__name__
            return result
        markers = set(completed.stdout.splitlines())
        proof = work / "proof"
        passed = (
            completed.returncode == 0
            and {"WORKSPACE_OK", "OUTSIDE_DENIED", "NETWORK_DENIED"} <= markers
            and sentinel.read_text() == "untouched"
            and proof.is_file()
            and proof.read_text() == "sandboxed"
        )
        result["sandbox_verified"] = bool(passed)
        if not passed:
            result["error"] = (completed.stderr.strip() or
                               "missing denial evidence or modified sentinel")[:240]
    return result


if __name__ == "__main__":
    outcome = inspect()
    print(json.dumps(outcome, indent=2, sort_keys=True))
    raise SystemExit(0 if outcome["sandbox_verified"] else 2)
