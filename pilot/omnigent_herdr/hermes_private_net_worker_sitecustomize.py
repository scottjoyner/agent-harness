"""Synthetic Hermes worker in separate private-network Docker container.

The normal rootless entrypoint and image-baked strict_executor_main run three
fresh processes: initial claim; API-offline failure; restart+lease takeover.
No Hermes CLI/provider or production secret/data is permitted.
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import subprocess
import time
from unittest.mock import patch
import requests
from assistx.agents import hermes_agent_adapter as agent

ROOT = pathlib.Path("/workspace")
STEP = ROOT / "stage-journal.json"
SOURCE = pathlib.Path("/research/knowledge/synthetic-source.txt")
assert SOURCE.read_text().strip() == "SYNTHETIC_ONLY"
assert os.geteuid() == 1000
os.environ["NO_PROXY"] = "*"  # only synthetic internal bridge is allowed
os.environ["no_proxy"] = "*"
base = os.environ["FAKE_ASSISTX_URL"]
assert re.fullmatch(r"http://og-hermes-net-server-[0-9a-f]{16}:18181", base), base
TASK_A, TASK_B = "synthetic-fault-a", "synthetic-fault-b"
AGENT = "hermes-synthetic-canary"
ADMIN = "SYNTHETIC_FIXTURE_CONTROL_ONLY"
_ORIGINAL_LOOP = agent.run_loop
calls = []

def forbidden_process(*_args, **_kwargs):
    raise AssertionError("model subprocess execution is forbidden")

def fake_engine(*args, **kwargs):
    assert kwargs.get("model") == "synthetic/tool-model"
    calls.append("synthetic_model_only")
    return {"success": True, "output": "SYNTHETIC_ONLY: no external execution",
            "session_id": None, "elapsed": 0.01}

def req(method, path, *, token, claim=None, status=None):
    return requests.request(method, base + path,
        headers={"Authorization": "Bearer " + token, "X-AssistX-Agent-Id": AGENT},
        json=({"claim_id": claim, "status": status} if claim is not None else {}),
        timeout=2)

def admin(path, body=None):
    result = requests.post(base + "/__fixture/" + path,
        headers={"Authorization": "Bearer " + ADMIN},
        json=body or {}, timeout=2)
    result.raise_for_status()

def error409(token, claim):
    response = req("POST", "/api/tasks/" + TASK_B + "/complete",
                   token=token, claim=claim, status="DONE")
    assert response.status_code == 409, response.status_code

def exercise():
    assert agent._strict_executor_auth_installed
    assert ROOT.joinpath("hermes-home/config.yaml").is_file()
    assert "NoNewPrivs:\t1" in pathlib.Path("/proc/self/status").read_text()
    assert "CapEff:\t0000000000000000" in pathlib.Path("/proc/self/status").read_text()
    for forbidden in ("/media/scott/SSD_4TB", "/media/scott/NAS5", "/root/knowledge"):
        try:
            assert not pathlib.Path(forbidden).exists(), forbidden
        except PermissionError:
            pass
    try:
        pathlib.Path("/research/knowledge/no").write_text("FAIL")
        raise AssertionError("knowledge unexpectedly writable")
    except OSError:
        pass
    stage = (json.loads(STEP.read_text())["stage"] if STEP.exists() else 0)
    if stage == 0:
        for retry in range(25):
            try:
                preflight = req("GET", "/api/agent/tasks?status=READY",
                                token="SYNTHETIC_BOOTSTRAP_ONLY")
                if preflight.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("private-network synthetic API not reachable")
        original_init = agent.AssistXClient.__init__
        def synthetic_init(self, *args, **kw):
            if not args and "base_url" not in kw:
                kw["base_url"] = base
            return original_init(self, *args, **kw)
        with patch.object(agent.AssistXClient, "__init__", synthetic_init), \
             patch.object(subprocess, "Popen", side_effect=forbidden_process), \
             patch.object(agent, "fleet", None), \
             patch.object(agent, "SELFTASK_BULK_MODELS", []), \
             patch.object(agent, "HERMES_DELEGATE_OPENCODE_TIERS", []), \
             patch.object(agent, "run_hermes", side_effect=fake_engine):
            _ORIGINAL_LOOP(once=True)
        assert calls == ["synthetic_model_only"]
        admin("unlock/" + TASK_B)
        client = agent.AssistXClient(base_url=base)
        claim = client.claim_task(TASK_B, "synthetic-private-net-original")
        assert claim and claim.get("claimed")
        client._heartbeat_stop.set()
        client._heartbeat_thread.join(timeout=2)
        original = {"stage": 1, "old_token": client.task_token,
                    "old_claim": claim["task"]["claim_id"]}
        assert original["old_token"].startswith("synthetic-task-token-")
        STEP.write_text(json.dumps(original, sort_keys=True) + "\n")
        print("HERMES_PRIVATE_NET_STAGE_1_PASS", flush=True)
    elif stage == 1:
        saved = json.loads(STEP.read_text())
        assert saved["old_token"].startswith("synthetic-task-token-")
        # The operator has stopped ONLY the synthetic server container.
        for op in ("heartbeat", "complete"):
            try:
                req("POST", "/api/tasks/" + TASK_B + "/" + op,
                    token=saved["old_token"], claim=saved["old_claim"],
                    status="DONE" if op == "complete" else "RUNNING")
                raise AssertionError("server unexpectedly reachable while stopped")
            except requests.ConnectionError:
                pass
        saved["stage"] = 2
        STEP.write_text(json.dumps(saved, sort_keys=True) + "\n")
        print("HERMES_PRIVATE_NET_STAGE_2_PASS", flush=True)
    elif stage == 2:
        saved = json.loads(STEP.read_text())
        client = agent.AssistXClient(base_url=base)
        # Preserved unexpired claim is not in the READY catalog.
        assert client.poll_tasks() == []
        admin("tick", {"seconds": 10})
        ready = client.poll_tasks()
        assert [r["id"] for r in ready] == [TASK_B]
        claim = client.claim_task(TASK_B, "synthetic-private-net-takeover")
        assert claim and claim.get("claimed")
        client._heartbeat_stop.set()
        client._heartbeat_thread.join(timeout=2)
        assert claim["task"]["claim_id"] != saved["old_claim"]
        assert client.task_token != saved["old_token"]
        error409(saved["old_token"], saved["old_claim"])
        error409(client.task_token, saved["old_claim"])
        client.heartbeat(TASK_B, "synthetic-private-net-takeover",
                         claim["task"]["claim_id"])
        current_token, current_claim = client.task_token, claim["task"]["claim_id"]
        client.complete_task(TASK_B, "synthetic-private-net-takeover",
                             "DONE", claim_id=current_claim)
        assert client.task_token == ""
        error409(current_token, current_claim)
        assert client.poll_tasks() == []
        saved.update(stage=3, new_claim=current_claim,
                     old_token_rejected=True, stale_claim_rejected=True,
                     completion_replay_rejected=True, worker_process_restarts=2,
                     synthetic_model_runs_first_stage=1, real_model_runs=0)
        STEP.write_text(json.dumps(saved, sort_keys=True) + "\n")
        print("HERMES_PRIVATE_NET_STAGE_3_PASS", flush=True)
    else:
        raise AssertionError("unexpected stage or reuse")
    assert not list(ROOT.rglob(".env"))

agent.run_loop = exercise
