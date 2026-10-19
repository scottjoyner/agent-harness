#!/usr/bin/env python3
"""Run ephemeral HMAC-scoped synthetic lease checks over a deny-only AF_UNIX socket.

No model, authenticated provider, external API, real repository, or persistent
broker is touched. This fixture does not confer production authorization.
"""
from __future__ import annotations

import hashlib
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

from capability_gate import SyntheticLeaseGate

REPLIES = [
    "synthetic_receipt_only",  # first valid synthetic receipt
    "denied",                  # replay
    "denied",                  # same UID but other owner
    "denied",                  # HMAC forgery
    "synthetic_receipt_only",  # second bounded receipt
    "denied",                  # quota exceeded
    "denied",                  # provider dispatch operation
    "denied",                  # revoked lease
    "denied",                  # previous epoch
    "denied",                  # altered requested model pin
]
EXPECTED_RECEIPTS = [1, None, None, None, 2, None, None, None, None, None]
MAX_MESSAGE_BYTES = 3072
MODEL = "kilo/kilo-auto/free"
OWNER = "owner-a"
SESSION = "session-a"

GUEST = r"""
import errno
import json
import os
import socket
import sys
from pathlib import Path

source = Path('/workspace/fixture.json')
data = json.loads(source.read_text(encoding='utf-8'))
token = data['owner_a']
requests = [
    {'schema': 1, 'op': 'synthetic_receipt', 'token': token, 'seq': 1},
    {'schema': 1, 'op': 'synthetic_receipt', 'token': token, 'seq': 1},
    {'schema': 1, 'op': 'synthetic_receipt', 'token': data['owner_b'], 'seq': 1},
    {'schema': 1, 'op': 'synthetic_receipt', 'token': data['forged'], 'seq': 2},
    {'schema': 1, 'op': 'synthetic_receipt', 'token': token, 'seq': 2},
    {'schema': 1, 'op': 'synthetic_receipt', 'token': token, 'seq': 3},
    {'schema': 1, 'op': 'dispatch', 'token': token, 'seq': 4},
    {'schema': 1, 'op': 'synthetic_receipt', 'token': data['revoked'], 'seq': 1},
    {'schema': 1, 'op': 'synthetic_receipt', 'token': data['old_epoch'], 'seq': 1},
    {'schema': 1, 'op': 'synthetic_receipt', 'token': data['wrong_model'], 'seq': 1},
]
replies = []
numbers = []
for request in requests:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        sock.connect('/run/fleet-broker/lease.sock')
        sock.sendall((json.dumps(request, sort_keys=True) + '\n').encode())
        sock.shutdown(socket.SHUT_WR)
        raw = sock.recv(4096)
    reply = json.loads(raw)
    if reply.get('dispatch_authorized') is not False:
        raise RuntimeError('broker offered agent dispatch authority')
    if reply.get('provider_requests_forwarded') != 0:
        raise RuntimeError('broker forwarded provider traffic')
    replies.append(reply.get('status'))
    numbers.append(reply.get('receipt_number'))

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as net:
    net.settimeout(1)
    direct_network_denied = net.connect_ex(('192.0.2.1', 443)) in (
        errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN)
status = dict(line.split(':', 1) for line in
              Path('/proc/self/status').read_text().splitlines() if ':' in line)
print(json.dumps({
    'statuses': replies, 'receipt_numbers': numbers,
    'direct_network_denied': direct_network_denied,
    'host_signing_key_unreadable': not Path(sys.argv[1]).exists(),
    'pid_isolated': os.getpid() <= 5 and sum(
        p.name.isdigit() for p in Path('/proc').iterdir()) <= 5,
    'capabilities_dropped': int(status.get('CapEff', '1').strip(), 16) == 0,
    'no_new_privs': status.get('NoNewPrivs', '').strip() == '1',
}, sort_keys=True))
"""


def command(binary: str, work: Path, transport: Path, host_key: Path) -> list[str]:
    return [
        binary, "--unshare-user", "--unshare-pid", "--unshare-net",
        "--unshare-ipc", "--unshare-uts", "--die-with-parent",
        "--new-session", "--clearenv", "--cap-drop", "ALL",
        "--dir", "/usr", "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
        "--dir", "/workspace", "--bind", str(work), "/workspace",
        "--dir", "/run", "--dir", "/run/fleet-broker",
        "--ro-bind", str(transport), "/run/fleet-broker",
        "--setenv", "HOME", "/workspace", "--setenv", "LANG", "C",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--", "/usr/bin/python3", "-c", GUEST, str(host_key),
    ]


def physical(*, which=shutil.which, runner=subprocess.run) -> dict:
    report = {
        "schema": 1, "observed_host": socket.gethostname(),
        "mode": "synthetic_hmac_lease_deny_only",
        "physical_lease_checks_passed": False,
        "replay_denied": False,
        "cross_owner_denied": False,
        "quota_denied": False,
        "revocation_denied": False,
        "epoch_denied": False,
        "hmac_forgery_denied": False,
        "scope_model_mismatch_denied": False,
        "forbidden_operations_denied": False,
        "broker_peer_uid_matched": False,
        "host_signing_key_unreadable": False,
        "sandbox_direct_network_denied": False,
        "host_key_unchanged": False,
        "provider_requests_forwarded": 0,
        "agent_dispatch_authorized": False,
        "model_egress_authorized": False,
        "used_real_repository": False,
        "errors": [],
    }
    binary = which("bwrap")
    if not binary:
        report["errors"].append("Bubblewrap is missing")
        return report
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="og-lease-", dir=cache) as td:
        root = Path(td)
        work, transport, guard = (root / name for name in
                                  ("work", "transport", "guard"))
        for item in (work, transport, guard):
            item.mkdir(mode=0o700)
        secret = secrets.token_bytes(32)
        host_key = guard / "host-signing-key"
        host_key.write_bytes(secret)
        os.chmod(host_key, 0o600)
        digest = hashlib.sha256(b"synthetic-disposable-worktree").hexdigest()
        gate = SyntheticLeaseGate(secret)
        old_epoch = gate.mint(uid=os.getuid(), owner=OWNER, session=SESSION,
                              worktree_sha256=digest, model_pin=MODEL)
        gate.rotate_epoch()
        scoped = dict(uid=os.getuid(), owner=OWNER, session=SESSION,
                      worktree_sha256=digest, model_pin=MODEL)
        token = gate.mint(**scoped)
        alternate_owner = gate.mint(**{**scoped, "owner": "owner-b"})
        revoked = gate.mint(**scoped)
        gate.revoke(revoked)
        wrong_model = gate.mint(**{**scoped, "model_pin": "paid/model"})
        body, sig = token.split(".")
        forged = body + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
        (work / "fixture.json").write_text(
            json.dumps({
                "owner_a": token, "owner_b": alternate_owner,
                "forged": forged, "revoked": revoked,
                "old_epoch": old_epoch, "wrong_model": wrong_model,
            }), encoding="utf-8")
        sockpath = transport / "lease.sock"
        ready = threading.Event()
        observed = {"handled": 0, "uid_ok": True}

        def serve() -> None:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                    server.bind(str(sockpath))
                    os.chmod(sockpath, 0o600)
                    server.listen(4)
                    server.settimeout(7)
                    ready.set()
                    for _ in REPLIES:
                        conn, _ = server.accept()
                        with conn:
                            conn.settimeout(3)
                            peer_raw = conn.getsockopt(socket.SOL_SOCKET,
                                                       socket.SO_PEERCRED, 12)
                            _, uid, _ = struct.unpack("3i", peer_raw)
                            observed["uid_ok"] &= uid == os.getuid()
                            raw = conn.recv(MAX_MESSAGE_BYTES + 1)
                            try:
                                req = (json.loads(raw) if len(raw) <= MAX_MESSAGE_BYTES
                                       else None)
                            except (ValueError, UnicodeDecodeError):
                                req = None
                            answer = gate.check(
                                req, peer_uid=uid, expected_owner=OWNER,
                                expected_session=SESSION,
                                expected_worktree_sha256=digest,
                                expected_model_pin=MODEL)
                            conn.sendall((json.dumps(answer) + "\n").encode())
                            observed["handled"] += 1
            except Exception:
                observed["error"] = "denied or aborted broker fixture"
                ready.set()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        if not ready.wait(3) or observed.get("error"):
            report["errors"].append("broker could not bind")
            return report
        try:
            completed = runner(
                command(binary, work, transport, host_key),
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=14,
            )
        except (OSError, subprocess.TimeoutExpired):
            report["errors"].append("sandbox unavailable or timed out")
            return report
        thread.join(timeout=8)
        try:
            witness = json.loads(completed.stdout)
        except ValueError:
            witness = {}
        statuses = witness.get("statuses", [])
        numbers = witness.get("receipt_numbers", [])
        report["replay_denied"] = len(statuses) == 10 and statuses[1] == "denied"
        report["cross_owner_denied"] = len(statuses) == 10 and statuses[2] == "denied"
        report["hmac_forgery_denied"] = len(statuses) == 10 and statuses[3] == "denied"
        report["quota_denied"] = len(statuses) == 10 and statuses[5] == "denied"
        report["forbidden_operations_denied"] = len(statuses) == 10 and statuses[6] == "denied"
        report["revocation_denied"] = len(statuses) == 10 and statuses[7] == "denied"
        report["epoch_denied"] = len(statuses) == 10 and statuses[8] == "denied"
        report["scope_model_mismatch_denied"] = len(statuses) == 10 and statuses[9] == "denied"
        report["broker_peer_uid_matched"] = (
            observed["uid_ok"] is True and observed["handled"] == len(REPLIES)
            and not observed.get("error"))
        report["sandbox_direct_network_denied"] = (
            witness.get("direct_network_denied") is True)
        report["host_signing_key_unreadable"] = (
            witness.get("host_signing_key_unreadable") is True)
        report["host_key_unchanged"] = host_key.read_bytes() == secret
        checks = (
            "replay_denied", "cross_owner_denied", "hmac_forgery_denied",
            "quota_denied", "forbidden_operations_denied", "revocation_denied",
            "epoch_denied", "scope_model_mismatch_denied",
            "broker_peer_uid_matched", "sandbox_direct_network_denied",
            "host_signing_key_unreadable", "host_key_unchanged",
        )
        report["physical_lease_checks_passed"] = (
            completed.returncode == 0 and statuses == REPLIES
            and numbers == EXPECTED_RECEIPTS
            and witness.get("pid_isolated") is True
            and witness.get("capabilities_dropped") is True
            and witness.get("no_new_privs") is True
            and all(report[key] for key in checks))
        if not report["physical_lease_checks_passed"]:
            report["errors"].append("lease or sandbox denial witness incomplete")
    return report


def exit_status(report: dict) -> int:
    return 0 if report.get("physical_lease_checks_passed") is True else 2


if __name__ == "__main__":
    proof = physical()
    print(json.dumps(proof, indent=2, sort_keys=True))
    raise SystemExit(exit_status(proof))
