"""Test-only normal-entrypoint harness, with an independent fake API process.

The subprocess is the ONLY allowed child and only executes checked-in fixture
Python within a Docker --network=none disposable container.
"""
from __future__ import annotations
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
from unittest.mock import patch
import requests
from assistx.agents import hermes_agent_adapter as agent

ROOT = pathlib.Path("/workspace")
FIXTURE = ROOT / "hermes_fault_api_server.py"
BOOT = "SYNTHETIC_BOOTSTRAP_ONLY"
ADMIN = "SYNTHETIC_FIXTURE_CONTROL_ONLY"
TASKS = ("synthetic-fault-a", "synthetic-fault-b", "synthetic-fault-c")
_ORIGINAL_LOOP = agent.run_loop
events = []
fake_engine_runs = [0]

def fake_engine(*args, **kwargs):
    assert kwargs.get("model") == "synthetic/tool-model"
    fake_engine_runs[0] += 1
    return {"success": True, "output": "SYNTHETIC_ONLY: valid fake task result",
            "session_id": None, "elapsed": 0.01}

def forbidden_process(*args, **kwargs):
    raise AssertionError("unexpected subprocess from Hermes worker")

def admin(base, path, value=None):
    r = requests.post(base + "/__fixture/" + path, json=value or {},
        headers={"Authorization": "Bearer " + ADMIN}, timeout=2)
    r.raise_for_status()

def start_server(port):
    # Isolated mode skips sitecustomize when executing this fixed local file.
    args = [sys.executable, "-I", "-B", str(FIXTURE), str(port),
            "/workspace/fake-assistx-fault-state.json"]
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env={"HOME": "/workspace", "PATH": "/usr/local/bin:/usr/bin:/bin",
             "PYTHONDONTWRITEBYTECODE": "1"}, close_fds=True)
    assert process.pid != os.getpid()
    base = "http://127.0.0.1:" + str(port)
    for _ in range(65):
        if process.poll() is not None:
            msg = process.stderr.read(1200).decode(errors="replace")
            raise RuntimeError("synthetic server failed at startup: " + msg)
        try:
            resp = requests.get(base + "/api/agent/tasks",
                headers={"Authorization": "Bearer " + BOOT,
                         "X-AssistX-Agent-Id": "hermes-synthetic-canary"},
                timeout=0.25)
            if resp.status_code == 200:
                return process, base
        except requests.RequestException:
            pass
        time.sleep(0.025)
    raise RuntimeError("bounded synthetic server startup timed out")

def kill_server(proc):
    if proc is None:
        return
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=3)
    proc.stderr.close()

def is_http_rejected(base, task_id, claim, token):
    response = requests.post(base + "/api/tasks/" + task_id + "/complete",
        headers={"Authorization": "Bearer " + token,
                 "X-AssistX-Agent-Id": "hermes-synthetic-canary"},
        json={"status": "DONE", "claim_id": claim}, timeout=3)
    assert response.status_code == 409, response.status_code

def exercise():
    assert os.geteuid() == 1000 and agent._strict_executor_auth_installed
    assert FIXTURE.is_file() and not FIXTURE.is_symlink()
    assert pathlib.Path("/research/knowledge/synthetic-source.txt").read_text().strip() == "SYNTHETIC_ONLY"
    assert (ROOT / "hermes-home/config.yaml").is_file()
    for forbidden in ("/media/scott/SSD_4TB", "/media/scott/NAS5", "/root/knowledge"):
        try:
            assert not pathlib.Path(forbidden).exists()
        except PermissionError:
            pass
    try:
        pathlib.Path("/research/knowledge/forbidden").write_text("FAIL")
        raise AssertionError("synthetic RO knowledge was writable")
    except OSError:
        pass
    assert "NoNewPrivs:\t1" in pathlib.Path("/proc/self/status").read_text()
    assert "CapEff:\t0000000000000000" in pathlib.Path("/proc/self/status").read_text()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = None
    try:
        process, base = start_server(port)
        agent.ASSISTX_URL = base
        # Strict client default URL was captured when auth was installed.
        # Redirect only this disposable test’s no-argument loop client.
        original_init = agent.AssistXClient.__init__
        def synthetic_init(self, *args, **kw):
            if not args and "base_url" not in kw:
                kw["base_url"] = base
            return original_init(self, *args, **kw)
        # Only the actual adapter code handles poll/claim/task token.
        with patch.object(agent.AssistXClient, "__init__", synthetic_init), \
             patch.object(subprocess, "Popen", side_effect=forbidden_process), \
             patch.object(agent, "fleet", None), \
             patch.object(agent, "SELFTASK_BULK_MODELS", []), \
             patch.object(agent, "HERMES_DELEGATE_OPENCODE_TIERS", []), \
             patch.object(agent, "run_hermes", side_effect=fake_engine):
            _ORIGINAL_LOOP(once=True)
        assert fake_engine_runs == [1]
        assert json.loads((ROOT / "fake-assistx-fault-state.json").read_text())["tasks"][TASKS[0]]["status"] == "DONE"
        events.append("real_http_done")
        admin(base, "unlock/" + TASKS[1])
        second = agent.AssistXClient(base_url=base)
        claimed_b = second.claim_task(TASKS[1], "synthetic-session-b")
        assert claimed_b["claimed"]
        second._heartbeat_stop.set()
        second._heartbeat_thread.join(timeout=2)
        token_b = second.task_token
        claim_b = claimed_b["task"]["claim_id"]
        second.heartbeat(TASKS[1], "synthetic-session-b", claim_b)
        admin(base, "revoke/" + TASKS[1])
        try:
            second.heartbeat(TASKS[1], "synthetic-session-b", claim_b)
            raise AssertionError("revoked heartbeat accepted")
        except requests.HTTPError as ex:
            assert ex.response.status_code == 409
        second._lease_lost.set()
        second.complete_task(TASKS[1], "synthetic-session-b", "DONE", claim_id=claim_b)
        assert not second.task_token
        is_http_rejected(base, TASKS[1], claim_b, token_b)
        events.append("revoked_stale_denied")
        admin(base, "unlock/" + TASKS[2])
        third = agent.AssistXClient(base_url=base)
        claimed_c = third.claim_task(TASKS[2], "synthetic-session-c")
        assert claimed_c["claimed"]
        third._heartbeat_stop.set()
        third._heartbeat_thread.join(timeout=2)
        old_token = third.task_token
        old_claim = claimed_c["task"]["claim_id"]
        third.heartbeat(TASKS[2], "synthetic-session-c", old_claim)
        kill_server(process)
        process = None
        try:
            third.heartbeat(TASKS[2], "synthetic-session-c", old_claim)
            raise AssertionError("heartbeat succeeded while server dead")
        except requests.ConnectionError:
            pass
        events.append("server_crash_detected")
        process, base = start_server(port)
        assert agent.AssistXClient(base_url=base).poll_tasks() == []
        events.append("restart_preserves_live_lease")
        admin(base, "tick", {"seconds": 10})
        fresh = agent.AssistXClient(base_url=base)
        assert [item["id"] for item in fresh.poll_tasks()] == [TASKS[2]]
        renewed = fresh.claim_task(TASKS[2], "synthetic-session-c-replacement")
        assert renewed["claimed"]
        fresh._heartbeat_stop.set()
        fresh._heartbeat_thread.join(timeout=2)
        new_claim = renewed["task"]["claim_id"]
        assert new_claim != old_claim and fresh.task_token != old_token
        is_http_rejected(base, TASKS[2], old_claim, old_token)
        # Also fail closed when a valid new token carries the old claim.
        is_http_rejected(base, TASKS[2], old_claim, fresh.task_token)
        fresh.heartbeat(TASKS[2], "synthetic-session-c-replacement", new_claim)
        fresh.complete_task(TASKS[2], "synthetic-session-c-replacement",
                            "DONE", claim_id=new_claim)
        assert not fresh.task_token
        events.append("expired_lease_reclaimed_old_fenced")
        kill_server(process)
        process = None
        process, base = start_server(port)
        assert agent.AssistXClient(base_url=base).poll_tasks() == []
        states = json.loads((ROOT / "fake-assistx-fault-state.json").read_text())["tasks"]
        assert [states[x]["status"] for x in TASKS] == ["DONE", "CANCELLED", "DONE"]
        assert fake_engine_runs == [1]
        assert not list(ROOT.rglob(".env"))
        (ROOT / "fault-result.json").write_text(json.dumps({
            "events": events, "separate_server_pid": process.pid != os.getpid(),
            "fake_engine_runs": fake_engine_runs[0],
            "server_starts": 3,
            "real_api_tasks": 0, "provider_requests": 0}, sort_keys=True) + "\n")
        print("HERMES_INDEPENDENT_FAKE_API_FAULT_PASS", flush=True)
    finally:
        kill_server(process)

agent.run_loop = exercise
