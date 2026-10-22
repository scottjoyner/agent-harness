#!/usr/bin/env python3
"""Deny-only Unix socket egress simulation in a network-disconnected sandbox.

Never forwards provider requests. Does not authenticate or run a model.
The narrowly mounted socket is a physical transport proof, not admission.
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

MAX_REQUEST_BYTES = 2048
GUEST_CODE = r'''
import errno
import json
import os
from pathlib import Path
import socket
import sys

sock_path, challenge, host_secret = sys.argv[1:]
results = []
requests = [
    {"schema": 1, "op": "probe", "challenge": challenge},
    {"schema": 1, "op": "dispatch", "provider": "synthetic"},
    {"schema": 1, "op": "shell", "command": "not-executed"},
    {"schema": 1, "op": "probe", "challenge": "incorrect"},
]
for request in requests:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        s.connect(sock_path)
        s.sendall((json.dumps(request) + "\n").encode())
        s.shutdown(socket.SHUT_WR)
        reply = b""
        while len(reply) < 4096:
            block = s.recv(4096)
            if not block:
                break
            reply += block
    results.append(json.loads(reply))
passed = (results[0].get("status") == "synthetic_probe_only"
          and results[0].get("dispatch_authorized") is False
          and all(r.get("status") == "denied"
                  and r.get("dispatch_authorized") is False
                  for r in results[1:]))
network_denied = False
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.settimeout(1)
    network_denied = s.connect_ex(("192.0.2.1", 443)) in (
        errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN)
secret_unreadable = not Path(host_secret).exists()
print(json.dumps({"broker_probe_only": passed, "network_denied": network_denied,
                  "host_secret_unreadable": secret_unreadable}))
'''


def decide(request: object, *, challenge: str, peer_uid: int | None,
           expected_uid: int) -> dict:
    """Deliberately NO provider forwarding or command execution."""
    denied = {"status": "denied", "dispatch_authorized": False}
    if peer_uid != expected_uid or not isinstance(request, dict):
        return denied
    if set(request) != {"schema", "op", "challenge"}:
        return denied
    if (type(request.get("schema")) is int and request["schema"] == 1
            and request.get("op") == "probe"
            and request.get("challenge") == challenge):
        return {"status": "synthetic_probe_only", "dispatch_authorized": False}
    return denied


def sandbox_args(bwrap: str, work: Path, transport_dir: Path,
                 secret: Path, challenge: str) -> list[str]:
    return [
        bwrap, "--unshare-user", "--unshare-pid", "--unshare-net",
        "--unshare-ipc", "--unshare-uts", "--die-with-parent",
        "--new-session", "--clearenv", "--cap-drop", "ALL",
        "--dir", "/usr", "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
        "--dir", "/workspace", "--bind", str(work), "/workspace",
        "--dir", "/run", "--dir", "/run/fleet-broker",
        "--ro-bind", str(transport_dir), "/run/fleet-broker",
        "--setenv", "HOME", "/workspace",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "LANG", "C",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--", "/usr/bin/python3", "-c", GUEST_CODE,
        "/run/fleet-broker/probe.sock", challenge, str(secret),
    ]


def physical(*, which=shutil.which, runner=subprocess.run) -> dict:
    result = {
        "schema": 1, "observed_host": socket.gethostname(),
        "synthetic_socket_verified": False,
        "sandbox_direct_network_denied": False,
        "sandbox_host_secret_unreadable": False,
        "broker_peer_uid_verified": False,
        "provider_requests_forwarded": 0,
        "agent_dispatch_authorized": False,
        "model_egress_authorized": False,
        "mode": "deny_only_unix_socket_fixture",
        "errors": [],
    }
    binary = which("bwrap")
    if not binary:
        result["errors"].append("bwrap missing")
        return result
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="og-socket-", dir=cache) as td:
        root = Path(td)
        work, transport, guard = root / "work", root / "broker", root / "guard"
        for path in (work, transport, guard):
            path.mkdir(mode=0o700)
        secret = guard / "private"
        secret.write_text("UNEXPOSED", encoding="utf-8")
        challenge = secrets.token_hex(16)
        sock_path = transport / "probe.sock"
        ready = threading.Event()
        observed = {"uid_ok": True, "handled": 0}

        def broker() -> None:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                    server.bind(str(sock_path))
                    os.chmod(sock_path, 0o600)
                    server.listen(4)
                    server.settimeout(7)
                    ready.set()
                    for _ in range(4):
                        with server.accept()[0] as conn:
                            conn.settimeout(3)
                            uid = None
                            if hasattr(socket, "SO_PEERCRED"):
                                cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                                _, uid, _ = struct.unpack("3i", cred)
                            observed["uid_ok"] &= uid == os.getuid()
                            data = conn.recv(MAX_REQUEST_BYTES + 1)
                            try:
                                request = (json.loads(data) if len(data) <= MAX_REQUEST_BYTES
                                           else None)
                            except (ValueError, UnicodeDecodeError):
                                request = None
                            response = decide(request, challenge=challenge,
                                              peer_uid=uid, expected_uid=os.getuid())
                            conn.sendall((json.dumps(response) + "\n").encode())
                            observed["handled"] += 1
            except Exception:
                observed["error"] = "broker_failed_without_model_forwarding"
                ready.set()

        thread = threading.Thread(target=broker, daemon=True)
        thread.start()
        if not ready.wait(4) or observed.get("error"):
            result["errors"].append("broker failed to bind")
            return result
        try:
            completed = runner(
                sandbox_args(binary, work, transport, secret, challenge),
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=13,
            )
        except (OSError, subprocess.TimeoutExpired):
            result["errors"].append("sandbox subprocess failed or timed out")
            return result
        thread.join(timeout=8)
        try:
            guest = json.loads(completed.stdout)
        except ValueError:
            guest = {}
        result["broker_peer_uid_verified"] = (
            observed.get("uid_ok") is True and observed.get("handled") == 4)
        result["sandbox_direct_network_denied"] = (
            guest.get("network_denied") is True)
        result["sandbox_host_secret_unreadable"] = (
            guest.get("host_secret_unreadable") is True and
            secret.read_text(encoding="utf-8") == "UNEXPOSED")
        result["synthetic_socket_verified"] = (
            completed.returncode == 0 and guest.get("broker_probe_only") is True
            and result["broker_peer_uid_verified"]
            and result["sandbox_direct_network_denied"]
            and result["sandbox_host_secret_unreadable"]
            and "error" not in observed)
        if not result["synthetic_socket_verified"]:
            result["errors"].append("synthetic transport or denial witness failed")
    return result


def exit_status(result: dict) -> int:
    """A failed physical safety check is a failing CLI status."""
    return 0 if result.get("synthetic_socket_verified") is True else 2


if __name__ == "__main__":
    report = physical()
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(exit_status(report))
