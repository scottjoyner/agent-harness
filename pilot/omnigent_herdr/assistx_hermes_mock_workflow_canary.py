#!/usr/bin/env python3
"""One-shot real Hermes Python poll/persistence canary with synthetic dependencies.

This is NOT agent dispatch or permission to deploy. No production mounts,
secrets, providers, network, source effects, or Docker Compose changes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import assistx_hermes_container_canary as shell

# The program imports application code from the pinned IMAGE itself, not from a
# live checkout. Any change to the image requires a fresh qualification.
SMOKE = r'''
import json
import os
import pathlib
import sys
from unittest.mock import patch

assert os.geteuid() != 0
assert os.getenv("ASSISTX_URL") == "http://127.0.0.1:1"
assert pathlib.Path("/research/knowledge/synthetic-source.txt").read_text().strip() == "SYNTHETIC_ONLY"
for denied in ("/media/scott/SSD_4TB", "/media/scott/NAS5", "/root/knowledge", "/root/.ssh/id_hermes"):
    try:
        assert not pathlib.Path(denied).exists(), denied
    except PermissionError:
        # Inaccessible root-owned parent is an acceptable stronger denial.
        pass
try:
    pathlib.Path("/research/knowledge/forbidden").write_text("NO")
    raise AssertionError("RO knowledge writable")
except OSError:
    pass
try:
    pathlib.Path("/etc/hermes-canary-forbidden").write_text("NO")
    raise AssertionError("rootfs writable")
except OSError:
    pass
status = pathlib.Path("/proc/self/status").read_text()
assert "NoNewPrivs:\t1" in status
assert "CapEff:\t0000000000000000" in status
sys.path.insert(0, "/app/src")
from assistx import strict_executor_main as entry
agent = entry.hermes_agent_adapter
calls = []
class FakeResponse:
    def raise_for_status(self):
        return None
    def json(self):
        return {"items": []}
def fake_http(method, url, **kwargs):
    assert method == "GET" and url.startswith("http://127.0.0.1:1/api/agent/tasks?"), url
    assert kwargs.get("headers", {}).get("Authorization") == "Bearer SYNTHETIC_BOOTSTRAP_ONLY"
    assert kwargs.get("headers", {}).get("X-AssistX-Agent-Id") == "hermes-synthetic-canary"
    calls.append(url)
    return FakeResponse()
with patch("requests.request", side_effect=fake_http), patch.object(agent, "fleet", None), patch.object(agent, "SELFTASK_BULK_MODELS", []):
    entry.install_strict_executor_adapter(agent)
    assert getattr(agent, "_strict_executor_auth_installed", False)
    agent.run_loop(once=True)
assert len(calls) == 1, "exactly one synthetic authenticated poll"
assert agent.ensure_model_env("synthetic/canary") == "/workspace/knowledge/synthetic_canary"
agent.record_task_eval("synthetic/canary", "synthetic", True, 0.1)
evaluations = agent.load_eval()
assert evaluations["models"]["synthetic/canary"]["tasks"]["synthetic"]["runs"] == 1
assert evaluations["models"]["synthetic/canary"]["tasks"]["synthetic"]["success"] == 1
assert pathlib.Path("/workspace/knowledge/synthetic_canary/ENV.md").is_file()
assert pathlib.Path("/workspace/registry/model-profiles.json").is_file()
print("ISOLATED_HERMES_PYTHON_WORKFLOW_PASS")
'''
SYNTHETIC_ENV = (
    "ASSISTX_URL=http://127.0.0.1:1",
    "ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN=SYNTHETIC_BOOTSTRAP_ONLY",
    "ASSISTX_STRICT_EXECUTOR_AUTH=true",
    "HERMES_AGENT_ID=hermes-synthetic-canary",
    "HERMES_EVAL_PATH=/workspace/registry/model-profiles.json",
    "HERMES_KNOWLEDGE_ROOT=/workspace/knowledge",
    "HERMES_SELFTASK_BULK_MODELS=",
    "HERMES_SELFTASK_MODELS=",
    "HERMES_SELFTASK_ENABLED=false",
    "HERMES_MAX_TASKS_PER_LOOP=1",
    "HOME=/workspace",
    "PYTHONDONTWRITEBYTECODE=1",
)
SUCCESS = "ISOLATED_HERMES_PYTHON_WORKFLOW_PASS"
BLOCKERS = [
    "production_hermes_mount_risk_unresolved",
    "physical_production_evidence_write_denial_unverified",
    "normal_agent_task_execution_unverified",
    "source_image_git_provenance_unverified",
    "authenticated_source_owner_revocation_unverified",
]
def make_argv(image: str, name: str, ro: Path, rw: Path) -> list[str]:
    cmd = shell.docker_create_argv(image, name, ro, rw)
    # Keep the prior guard's exact image, entrypoint, RO root, mounts, user,
    # network, and capabilities. Replace only its synthetic shell program.
    assert cmd[-2] == "-c"
    cmd[-1] = "python3 -B /workspace/smoke.py"
    at = cmd.index("--entrypoint=/bin/sh")
    cmd[at:at] = [f"--env={item}" for item in SYNTHETIC_ENV]
    return cmd

def physical_run(image: str) -> bool:
    name = "og-hermes-image-canary-" + uuid.uuid4().hex[:20]
    success = False
    removed = False
    with tempfile.TemporaryDirectory(prefix="og-hermes-containment-", dir=shell.SCRATCH_PARENT) as tmp:
        root = Path(tmp)
        root.chmod(0o755)
        ro, rw = root / "knowledge", root / "worktree"
        ro.mkdir(mode=0o755)
        rw.mkdir(mode=0o755)
        (ro / "synthetic-source.txt").write_text("SYNTHETIC_ONLY\n")
        (ro / "synthetic-source.txt").chmod(0o644)
        smoke = rw / "smoke.py"
        smoke.write_text(SMOKE)
        smoke.chmod(0o644)
        try:
            args = make_argv(image, name, ro, rw)
            created = shell._exec(args, timeout=25)
            if created.returncode:
                raise shell.UnsafeCanary("synthetic container create failed")
            shell._inspect_created(name, image, {str(ro), str(rw)})
            run = shell._exec([shell.DOCKER, "start", "--attach", name], timeout=45)
            exit_code = shell._exec([shell.DOCKER, "inspect", "--format", "{{.State.ExitCode}}", name], timeout=8)
            success = (run.returncode == 0 and run.stdout.strip() == SUCCESS
                       and exit_code.returncode == 0 and exit_code.stdout.strip() == "0")
            if not success:
                # Local failure class only; never emit synthetic HTTP bodies or environment.
                import re
                import sys
                classes = re.findall(r'(?m)^([A-Za-z][A-Za-z0-9_]*(?:Error|Exception)):', run.stderr)
                paths = re.findall(r"PermissionError: .*?: '([^']+)'", run.stderr)
                pathkind = next((p for p in paths[-1:] if p.startswith(('/workspace/', '/research/', '/etc/'))), 'other')
                functions = re.findall(r'(?m)^  File .*, in ([A-Za-z_][A-Za-z0-9_]*)$', run.stderr)
                print('SYNTHETIC_DIAGNOSTIC rc=' + str(run.returncode) + ' exit=' + exit_code.stdout.strip() + ' errors=' + ','.join(classes[-4:]) + ' path=' + pathkind + ' frames=' + ','.join(functions[-4:]), file=sys.stderr)
                raise shell.UnsafeCanary("synthetic Hermes Python smoke failed")
            if not (rw / "registry/model-profiles.json").is_file():
                raise shell.UnsafeCanary("synthetic eval file missing")
        finally:
            shell._exec([shell.DOCKER, "rm", "--force", name], timeout=12)
            removed = shell._exec([shell.DOCKER, "inspect", "--format", "{{.Id}}", name], timeout=6).returncode != 0
            if not removed:
                raise shell.UnsafeCanary("disposable container cleanup unverified")
    return success and removed

def capture(physical: bool = False) -> dict:
    data = {
        "schema": 1, "mode": "assistx_hermes_synthetic_python_workflow",
        "host": "x1-370", "physical_canary_executed": False,
        "exact_running_image_object_sha256": None,
        "synthetic_strict_poll_and_scoped_writes_passed": False,
        "provider_requests_forwarded": 0,
        "real_agent_tasks_executed": 0,
        "production_containers_modified": False,
        "production_deployment_authorized": False,
        "decision": "HOLD", "blockers": BLOCKERS.copy(),
    }
    if not physical:
        return data
    try:
        if os.uname().nodename != "x1-370":
            raise shell.UnsafeCanary("physical run restricted to x1-370")
        shell.SCRATCH_PARENT.mkdir(parents=True, exist_ok=True, mode=0o700)
        image = shell._image_id()
        data["exact_running_image_object_sha256"] = image
        if not physical_run(image):
            raise shell.UnsafeCanary("synthetic workflow incomplete")
        if shell._image_id() != image:
            raise shell.UnsafeCanary("production image changed mid-test")
        data["physical_canary_executed"] = True
        data["synthetic_strict_poll_and_scoped_writes_passed"] = True
    except (shell.UnsafeCanary, OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        data["blockers"].append("synthetic_python_workflow_failed_closed")
    return data

def verify(data: object) -> bool:
    baseline = capture()
    if not isinstance(data, dict) or set(data) != set(baseline):
        return False
    for key in ("schema", "mode", "host", "provider_requests_forwarded",
                "real_agent_tasks_executed", "production_containers_modified",
                "production_deployment_authorized", "decision"):
        if type(data[key]) != type(baseline[key]) or data[key] != baseline[key]:
            return False
    if (type(data["physical_canary_executed"]) is not bool
            or type(data["synthetic_strict_poll_and_scoped_writes_passed"]) is not bool
            or data["physical_canary_executed"] != data["synthetic_strict_poll_and_scoped_writes_passed"]
            or not isinstance(data["blockers"], list)
            or any(x not in data["blockers"] for x in BLOCKERS)):
        return False
    pinned = data["exact_running_image_object_sha256"]
    if pinned is not None and (not isinstance(pinned, str) or not shell.IMAGE_ID_RE.fullmatch(pinned)):
        return False
    return not data["physical_canary_executed"] or pinned is not None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-mock-canary", action="store_true")
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args()
    result = capture(opts.physical_mock_canary)
    if not verify(result):
        raise SystemExit("invalid Hermes workflow canary record")
    print(json.dumps(result, indent=2 if opts.json else None, sort_keys=True))
    if opts.physical_mock_canary and not result["synthetic_strict_poll_and_scoped_writes_passed"]:
        raise SystemExit(2)
