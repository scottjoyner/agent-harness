"""Two fresh image-baked Hermes processes: synthetic RO read + RW state restart.

Never mount or run with live tasks, providers or production paths.
The adapter currently conflates knowledge READ and self-task WRITE roots.
This fixture explicitly demonstrates the split-root gap without fixing it
silently; registry and outputs are written ONLY into disposable /workspace.
"""
from __future__ import annotations
import hashlib
import json
import os
import pathlib
import subprocess
from unittest.mock import patch
import requests
from assistx.agents import hermes_agent_adapter as adapter

ROOT = pathlib.Path("/workspace")
RO = pathlib.Path("/research/knowledge")
STAGE = ROOT / "knowledge-stage.json"
OUT = ROOT / "outputs" / "synthetic-summary.md"
EXPECTED = ("SYNTHETIC_INPUT_DECISION_A", "SYNTHETIC_INPUT_NOTE_B")
original_loop = adapter.run_loop
polls = [0]

class MockReply:
    def raise_for_status(self):
        return None
    def json(self):
        return {"items": []}

def synthetic_http(method, url, **kwargs):
    assert method == "GET" and url.startswith("http://127.0.0.1:1/api/agent/tasks?")
    assert kwargs["headers"]["Authorization"] == "Bearer SYNTHETIC_BOOTSTRAP_ONLY"
    assert kwargs["headers"]["X-AssistX-Agent-Id"] == "hermes-synthetic-canary"
    polls[0] += 1
    return MockReply()

def deny_process(*args, **kwargs):
    raise AssertionError("untrusted Hermes child execution in synthetic fixture")

def load_stage():
    return json.loads(STAGE.read_text()) if STAGE.is_file() else {"stage": 0}

def verify_paths():
    assert os.geteuid() == 1000 and adapter._strict_executor_auth_installed
    assert (ROOT / "hermes-home/config.yaml").is_file()
    assert (ROOT / ".hermes/config.yaml").is_symlink()
    assert not list(ROOT.rglob(".env"))
    status = pathlib.Path("/proc/self/status").read_text()
    assert "NoNewPrivs:\t1" in status
    assert "CapEff:\t0000000000000000" in status
    for forbidden in ("/media/scott/SSD_4TB", "/media/scott/NAS5",
                      "/root/knowledge", "/root/.ssh/id_hermes"):
        try:
            assert not pathlib.Path(forbidden).exists()
        except PermissionError:
            pass
    assert RO.is_dir() and RO.joinpath("synthetic-source.txt").read_text().strip() == "SYNTHETIC_ONLY"
    assert adapter.EVAL_PATH == str(ROOT / "eval.json")
    assert adapter.KNOWLEDGE_ROOT == str(ROOT / "knowledge")
    # The rendered Compose config has NO route from KNOWLEDGE_ROOT to the
    # read-only input corpus. This is an actual adapter compatibility gap.
    original_snapshot = adapter._gather_knowledge_context()
    assert not any(marker in original_snapshot for marker in EXPECTED), (
        "normal configured root does not reach readonly corpus")
    try:
        RO.joinpath("forbidden-write").write_text("DENIED")
        raise AssertionError("read-only source was writable")
    except OSError:
        pass
    try:
        pathlib.Path("/etc/forbidden-knowledge-fixture").write_text("DENIED")
        raise AssertionError("read-only rootfs was writable")
    except OSError:
        pass
    # Only within this synthetic test, remap adapter READ root to RO.
    # Production adapter does not yet expose independent read/write roots.
    adapter.KNOWLEDGE_ROOT = str(RO)
    snapshot = adapter._gather_knowledge_context(max_chars=2500)
    assert all(x in snapshot for x in EXPECTED), snapshot
    assert "non-synthetic" not in snapshot
    assert not list(RO.rglob(".env"))
    return snapshot

def synthetic_loop():
    phase = load_stage()
    assert phase["stage"] in (0, 1), "unexpected stale stage or replay"
    snapshot = verify_paths()
    snapshot_hash = hashlib.sha256(snapshot.encode()).hexdigest()
    with patch("requests.request", side_effect=synthetic_http), \
         patch.object(subprocess, "Popen", side_effect=deny_process), \
         patch.object(adapter, "fleet", None), \
         patch.object(adapter, "SELFTASK_BULK_MODELS", []), \
         patch.object(adapter, "HERMES_DELEGATE_OPENCODE_TIERS", []):
        original_loop(once=True)
    assert polls == [1]
    if phase["stage"] == 0:
        assert not (ROOT / "eval.json").exists()
        adapter.record_task_eval("synthetic/model", "snapshot", True, 1.0)
        registry = adapter.load_eval()
        assert registry["models"]["synthetic/model"]["tasks"]["snapshot"]["runs"] == 1
        OUT.parent.mkdir()
        OUT.write_text("SYNTHETIC_OUTPUT_ONLY\n" + snapshot_hash + "\n")
        # Existing adapter inconsistently uses env ROOT for bootstrap writes
        # but module KNOWLEDGE_ROOT for reading and self-task writes.
        workspace_model = adapter.ensure_model_env("synthetic/model")
        assert workspace_model == str(ROOT / "knowledge" / "synthetic_model")
        assert (ROOT / "knowledge" / "synthetic_model" / "ENV.md").is_file()
        # Real self-task output would still go into read-only corpus.
        assert str(RO / adapter._SELFTASK_TARGETS["bulk_summarize"]) == "/research/knowledge/SUMMARY.md"
        STAGE.write_text(json.dumps({
            "stage": 1, "source_digest": snapshot_hash,
            "split_root_gap_detected": True, "model_calls": 0,
            "provider_requests": 0}, sort_keys=True) + "\n")
        print("HERMES_KNOWLEDGE_RESTART_STAGE_1_PASS", flush=True)
    else:
        assert phase["source_digest"] == snapshot_hash
        assert phase["split_root_gap_detected"] is True
        assert OUT.read_text() == "SYNTHETIC_OUTPUT_ONLY\n" + snapshot_hash + "\n"
        assert adapter.load_eval()["models"]["synthetic/model"]["tasks"]["snapshot"]["runs"] == 1
        adapter.record_task_eval("synthetic/model", "snapshot", True, 2.0)
        record = adapter.load_eval()["models"]["synthetic/model"]["tasks"]["snapshot"]
        assert record["runs"] == 2 and record["success"] == 2
        assert record["avg_seconds"] == 1.5
        assert (ROOT / "hermes-home/config.yaml").is_file()
        # Re-check the source did not change across the container restart.
        assert RO.joinpath("notes", "context.txt").read_text().strip() == EXPECTED[1]
        phase.update(stage=2, persisted_eval_runs=2,
                     persisted_synthetic_output=True,
                     source_unmodified=True, model_calls=0,
                     provider_requests=0)
        STAGE.write_text(json.dumps(phase, sort_keys=True) + "\n")
        print("HERMES_KNOWLEDGE_RESTART_STAGE_2_PASS", flush=True)
    assert not list(ROOT.rglob(".env"))

adapter.run_loop = synthetic_loop
