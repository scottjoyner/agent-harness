"""Test-only localhost HTTP AssistX server loaded inside a disposable Hermes image.

Executed via PYTHONPATH sitecustomize by the NORMAL rootless entrypoint.
It performs real HTTP over loopback with Docker --network=none. No provider,
external API, real shell command, production mounts or production task IDs.
"""
from __future__ import annotations
import json
import os
import pathlib
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import requests
from assistx.agents import hermes_agent_adapter as agent

ROOT = pathlib.Path("/workspace")
JOURNAL = ROOT / "fake-assistx-committed.json"
RESTART = JOURNAL.exists()
EVENTS = []
TASK = "synthetic-http-task-001"
REVOKE = "synthetic-revoked-task-001"
AGENT = "hermes-synthetic-canary"
BOOT = "SYNTHETIC_BOOTSTRAP_ONLY"
TASK_TOKEN = "SYNTHETIC_CLAIM_ONLY"
CLAIMS = {TASK: "synthetic-http-claim-001", REVOKE: "synthetic-revoked-claim-001"}
STATE = {"completed": RESTART, "revoked": False}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def reply(self, status, value):
        raw = json.dumps(value, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.handle_task("GET")

    def do_POST(self):
        self.handle_task("POST")

    def handle_task(self, method):
        assert self.client_address[0] == "127.0.0.1"
        assert self.headers.get("X-AssistX-Agent-Id") == AGENT
        path = self.path.split("?", 1)[0]
        is_boot = path == "/api/agent/tasks" or path.endswith("/claim") or path.startswith("/api/executor/claims/")
        expected = BOOT if is_boot else TASK_TOKEN
        assert self.headers.get("Authorization") == "Bearer " + expected
        size = int(self.headers.get("Content-Length", "0"))
        assert 0 <= size <= 100000
        body = json.loads(self.rfile.read(size)) if size else {}
        assert isinstance(body, dict)
        result = None
        code = 200
        event = None
        if method == "GET" and path == "/api/agent/tasks":
            event = "poll"
            result = {"items": [] if STATE["completed"] else [{
                "id": TASK, "title": "Synthetic localhost HTTP task",
                "description": "No production actions", "payload": {"model": "synthetic/tool-model"}}]}
        elif method == "POST" and path in (f"/api/tasks/{TASK}/claim", f"/api/tasks/{REVOKE}/claim"):
            ident = path.split("/")[3]
            event = "claim:" + ident
            if (ident == TASK and STATE["completed"]) or (ident == REVOKE and RESTART):
                code, result = 409, {"detail": "synthetic claim fenced"}
            else:
                result = {"claimed": True, "task": {"claim_id": CLAIMS[ident]}}
        elif method == "POST" and path in (f"/api/executor/claims/{TASK}/token", f"/api/executor/claims/{REVOKE}/token"):
            ident = path.split("/")[4]
            event = "token:" + ident
            assert body.get("claim_id") == CLAIMS[ident]
            result = {"token": TASK_TOKEN, "claims": {"task_id": ident, "claim_id": CLAIMS[ident], "agent_id": AGENT}}
        elif method == "POST" and path == f"/api/tasks/{TASK}/heartbeat":
            event = "heartbeat:main"
            assert body.get("claim_id") == CLAIMS[TASK]
            result = {"ok": True}
        elif method == "POST" and path == f"/api/tasks/{REVOKE}/heartbeat":
            event = "heartbeat:revoked" if STATE["revoked"] else "heartbeat:revoke_initial"
            assert body.get("claim_id") == CLAIMS[REVOKE]
            code, result = (409, {"detail": "synthetic lease revoked"}) if STATE["revoked"] else (200, {"ok": True})
        elif method == "POST" and path == "/api/brain/context":
            event, result = "context", {"context_packet": {"references": []}}
        elif method == "POST" and path == "/api/memory/items":
            event, result = "memory", {"memory_item_id": "synthetic-memory-only"}
        elif method == "POST" and path == f"/api/tasks/{TASK}/complete":
            event = "complete:main"
            assert body.get("status") == "DONE" and body.get("claim_id") == CLAIMS[TASK]
            STATE["completed"] = True
            result = {"ok": True}
        elif method == "POST" and path == f"/api/tasks/{REVOKE}/complete":
            event = "complete:revoked_denied"
            code, result = 409, {"detail": "synthetic lease revoked"}
        else:
            raise AssertionError("unexpected fake HTTP path or method")
        EVENTS.append(event)
        self.reply(code, result)

SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
SERVER.daemon_threads = True
threading.Thread(target=SERVER.serve_forever, daemon=True, name="synthetic-http-assistx").start()
agent.ASSISTX_URL = "http://127.0.0.1:" + str(SERVER.server_port)
_ORIGINAL_LOOP = agent.run_loop

def forbid_process(*args, **kwargs):
    raise AssertionError("test must never spawn Hermes or another executable")

def fake_engine(*args, **kwargs):
    assert kwargs.get("model") == "synthetic/tool-model"
    return {"success": True, "output": "SYNTHETIC_ONLY: fake task completed locally with no provider",
            "session_id": None, "elapsed": 0.01}

def synthetic_real_http_loop():
    assert os.geteuid() == 1000
    assert agent._strict_executor_auth_installed
    for forbidden in ("/media/scott/SSD_4TB", "/media/scott/NAS5", "/root/knowledge"):
        try:
            assert not pathlib.Path(forbidden).exists(), forbidden
        except PermissionError:
            pass
    assert (ROOT / "hermes-home/config.yaml").is_file()
    status = pathlib.Path("/proc/self/status").read_text()
    assert "NoNewPrivs:\t1" in status and "CapEff:\t0000000000000000" in status
    try:
        pathlib.Path("/research/knowledge/should-not-write").write_text("FAIL")
        raise AssertionError("synthetic knowledge is writable")
    except OSError:
        pass
    strict_run_hermes = agent.run_hermes
    with patch.object(subprocess, "Popen", side_effect=forbid_process), \
         patch.object(agent, "fleet", None), \
         patch.object(agent, "SELFTASK_BULK_MODELS", []), \
         patch.object(agent, "HERMES_DELEGATE_OPENCODE_TIERS", []), \
         patch.object(agent, "run_hermes", side_effect=fake_engine):
        _ORIGINAL_LOOP(once=True)
        if not RESTART:
            assert STATE["completed"], "main task did not complete"
            assert EVENTS[:8] == ["poll", "claim:" + TASK, "token:" + TASK,
                "heartbeat:main", "context", "heartbeat:main",
                "memory", "complete:main"], EVENTS
            client = agent.AssistXClient()
            assert client.claim_task(REVOKE, "synthetic-revoked-session")["claimed"]
            STATE["revoked"] = True
            try:
                client.heartbeat(REVOKE, "synthetic-revoked-session", CLAIMS[REVOKE])
                raise AssertionError("HTTP 409 lease loss not enforced")
            except requests.HTTPError as exc:
                assert exc.response.status_code == 409
            before = EVENTS.count("complete:revoked_denied")
            client._lease_lost.set()
            client.complete_task(REVOKE, "synthetic-revoked-session", "DONE", claim_id=CLAIMS[REVOKE])
            assert EVENTS.count("complete:revoked_denied") == before
            assert client.task_token == ""
            stale = requests.post(agent.ASSISTX_URL + "/api/tasks/" + REVOKE + "/complete",
                headers={"Authorization": "Bearer " + TASK_TOKEN, "X-AssistX-Agent-Id": AGENT},
                json={"status": "DONE", "claim_id": CLAIMS[REVOKE]}, timeout=3)
            assert stale.status_code == 409
            assert EVENTS.count("complete:revoked_denied") == before + 1
            JOURNAL.write_text(json.dumps({"schema": 1, "state": "DONE", "task": TASK}) + "\n")
        else:
            assert json.loads(JOURNAL.read_text()) == {"schema": 1, "state": "DONE", "task": TASK}
            assert EVENTS == ["poll"], EVENTS
            assert strict_run_hermes("synthetic")["error"] == "executor_task_token_missing"
        assert not list(ROOT.rglob(".env"))
        assert pathlib.Path("/research/knowledge/synthetic-source.txt").read_text().strip() == "SYNTHETIC_ONLY"
        (ROOT / ("http-stage-2.json" if RESTART else "http-stage-1.json")).write_text(
            json.dumps({"events": EVENTS, "restart": RESTART, "real_http": True}, sort_keys=True) + "\n")
    SERVER.shutdown()
    print("ROOTLESS_HERMES_HTTP_RESTART_PASS" if RESTART else "ROOTLESS_HERMES_HTTP_FIRST_PASS", flush=True)

agent.run_loop = synthetic_real_http_loop
