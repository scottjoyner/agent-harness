"""Disposable localhost-only AssistX state machine for Hermes failure injection.

Never mount or run in production. The state file lives under a synthetic
workspace in a Docker network=none container; fixture admin calls are local.
"""
from __future__ import annotations
import json
import os
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

assert len(sys.argv) == 3 and os.geteuid() == 1000
PORT = int(sys.argv[1])
STATE_FILE = pathlib.Path(sys.argv[2])
assert 1024 < PORT < 65536
assert str(STATE_FILE) == "/workspace/fake-assistx-fault-state.json"
assert not STATE_FILE.is_symlink()
TASKS = ("synthetic-fault-a", "synthetic-fault-b", "synthetic-fault-c")
AGENT = "hermes-synthetic-canary"
BOOT = "SYNTHETIC_BOOTSTRAP_ONLY"
ADMIN = "SYNTHETIC_FIXTURE_CONTROL_ONLY"

def fresh():
    return {"clock": 100, "counter": 0, "tasks": {
        task: {"status": "READY" if task == TASKS[0] else "LOCKED",
               "claim": "", "token": "", "deadline": 0}
        for task in TASKS}}

def read_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else fresh()

def save_state(s):
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, sort_keys=True))
    tmp.replace(STATE_FILE)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, code, content):
        raw = json.dumps(content, sort_keys=True).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method):
        assert self.client_address[0] == "127.0.0.1"
        size = int(self.headers.get("Content-Length", 0))
        if size < 0 or size > 8192:
            return self.respond(413, {})
        try:
            data = json.loads(self.rfile.read(size)) if size else {}
        except ValueError:
            return self.respond(400, {})
        if not isinstance(data, dict):
            return self.respond(400, {})
        path = self.path.split("?", 1)[0]
        auth = self.headers.get("Authorization")
        s = read_state()
        if method == "POST" and path.startswith("/__fixture/"):
            if auth != "Bearer " + ADMIN:
                return self.respond(403, {})
            parts = path.split("/")
            if path == "/__fixture/tick":
                amount = data.get("seconds")
                if type(amount) is not int or not 1 <= amount <= 100:
                    return self.respond(400, {})
                s["clock"] += amount
            elif len(parts) == 4 and parts[2] in ("unlock", "revoke") and parts[3] in TASKS:
                task = s["tasks"][parts[3]]
                if parts[2] == "unlock" and task["status"] == "LOCKED":
                    task["status"] = "READY"
                elif parts[2] == "revoke" and task["status"] == "RUNNING":
                    task["status"] = "CANCELLED"
                else:
                    return self.respond(409, {})
            else:
                return self.respond(404, {})
            save_state(s)
            return self.respond(200, {"ok": True})
        if self.headers.get("X-AssistX-Agent-Id") != AGENT:
            return self.respond(403, {})
        bootstrap = path == "/api/agent/tasks" or path.endswith("/claim") or path.startswith("/api/executor/claims/")
        if bootstrap and auth != "Bearer " + BOOT:
            return self.respond(403, {})
        for task in s["tasks"].values():
            if task["status"] == "RUNNING" and s["clock"] >= task["deadline"]:
                task["status"] = "READY"
        parts = path.split("/")
        if method == "GET" and path == "/api/agent/tasks":
            items = [{"id": key, "title": "Synthetic fault task",
                      "description": "Synthetic no execution",
                      "payload": {"model": "synthetic/tool-model"}}
                     for key, value in s["tasks"].items() if value["status"] == "READY"]
            save_state(s)
            return self.respond(200, {"items": items})
        if method == "POST" and len(parts) == 5 and parts[1:3] == ["api", "tasks"] and parts[3] in TASKS:
            ident, op = parts[3], parts[4]
            task = s["tasks"][ident]
            if op == "claim":
                if task["status"] != "READY" or data.get("agent_id") != AGENT:
                    return self.respond(409, {"detail": "lease not claimable"})
                s["counter"] += 1
                claim = "synthetic-claim-" + str(s["counter"])
                task.update(status="RUNNING", claim=claim,
                            token="synthetic-task-token-" + str(s["counter"]),
                            deadline=s["clock"] + 5)
                save_state(s)
                return self.respond(200, {"claimed": True, "task": {"claim_id": claim}})
            valid = (task["status"] == "RUNNING"
                     and s["clock"] < task["deadline"]
                     and data.get("claim_id") == task["claim"]
                     and auth == "Bearer " + task["token"])
            if not valid:
                save_state(s)
                return self.respond(409, {"detail": "synthetic stale lease"})
            if op == "heartbeat":
                task["deadline"] = s["clock"] + 5
                save_state(s)
                return self.respond(200, {"ok": True})
            if op == "complete" and data.get("status") == "DONE":
                task["status"] = "DONE"
                save_state(s)
                return self.respond(200, {"ok": True})
        if method == "POST" and len(parts) == 6 and parts[1:4] == ["api", "executor", "claims"] and parts[4] in TASKS and parts[5] == "token":
            ident = parts[4]
            task = s["tasks"][ident]
            if task["status"] != "RUNNING" or data.get("claim_id") != task["claim"]:
                return self.respond(409, {"detail": "claim invalid"})
            return self.respond(200, {"token": task["token"],
                         "claims": {"task_id": ident, "claim_id": task["claim"], "agent_id": AGENT}})
        if method == "POST" and path in ("/api/brain/context", "/api/memory/items"):
            active = any(v["status"] == "RUNNING" and s["clock"] < v["deadline"]
                         and auth == "Bearer " + v["token"] for v in s["tasks"].values())
            if not active:
                return self.respond(409, {"detail": "no active synthetic lease"})
            return self.respond(200, {"context_packet": {"references": []}} if path.endswith("context")
                                else {"memory_item_id": "synthetic-only"})
        return self.respond(404, {})

server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
server.daemon_threads = True
server.serve_forever(poll_interval=0.05)
