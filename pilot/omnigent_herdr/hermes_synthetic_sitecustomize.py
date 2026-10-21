"""Site-only synthetic poll fixture for a DISPOSABLE, networkless Hermes image.

Never ship or mount this fixture into a real execution environment.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
from unittest.mock import patch

import requests
from assistx.agents import hermes_agent_adapter as adapter

_real_run_loop = adapter.run_loop
_count = [0]

class MockResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"items": []}

def synthetic_request(method, url, **kwargs):
    assert method == "GET"
    assert url.startswith("http://127.0.0.1:1/api/agent/tasks?")
    hdr = kwargs.get("headers", {})
    assert hdr.get("Authorization") == "Bearer SYNTHETIC_BOOTSTRAP_ONLY"
    assert hdr.get("X-AssistX-Agent-Id") == "hermes-synthetic-canary"
    _count[0] += 1
    return MockResponse()

def forbidden_process(*_args, **_kwargs):
    raise RuntimeError("unexpected process launch in synthetic poll")

def synthetic_single_loop():
    root = pathlib.Path("/workspace")
    assert os.geteuid() != 0
    assert adapter._strict_executor_auth_installed
    assert (root / "hermes-home/config.yaml").is_file()
    assert (root / ".hermes/config.yaml").is_symlink()
    assert not (root / ".hermes/.env").exists()
    assert not (root / "hermes-home/.env").exists()
    status = pathlib.Path("/proc/self/status").read_text()
    assert "NoNewPrivs:\t1" in status
    assert "CapEff:\t0000000000000000" in status
    for forbidden in ("/media/scott/SSD_4TB", "/media/scott/NAS5",
                      "/root/knowledge", "/root/.ssh/id_hermes"):
        try:
            assert not pathlib.Path(forbidden).exists()
        except PermissionError:
            pass
    try:
        pathlib.Path("/etc/hermes-rootless-canary-denied").write_text("FAIL")
        raise AssertionError("image root unexpectedly writable")
    except OSError:
        pass
    try:
        pathlib.Path("/research/knowledge/denied").write_text("FAIL")
        raise AssertionError("synthetic knowledge unexpectedly writable")
    except OSError:
        pass
    with patch("requests.request", side_effect=synthetic_request), \
         patch.object(subprocess, "Popen", side_effect=forbidden_process), \
         patch.object(adapter, "fleet", None), \
         patch.object(adapter, "SELFTASK_BULK_MODELS", []):
        _real_run_loop(once=True)
    assert _count[0] == 1
    (root / "verified.marker").write_text("ROOTLESS_HERMES_NORMAL_MAIN_PASS\n")
    print("ROOTLESS_HERMES_NORMAL_MAIN_PASS")

# The unmodified strict_executor_main.main() calls this wrapper after
# installing strict executor auth. Only the looping test horizon is reduced.
adapter.run_loop = synthetic_single_loop
