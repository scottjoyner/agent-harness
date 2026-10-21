#!/usr/bin/env python3
"""Render and strictly verify isolated Hermes Compose; NEVER starts Docker."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

IMAGE_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
ROOT_RE = re.compile(r"^og-hermes-containment-[A-Za-z0-9_]{8}$")
SCRATCH_PARENT = Path("/home/scott/.cache")
COMPOSE = Path(__file__).with_name("hermes_rootless_candidate.compose.yaml")
SERVICE = "hermes-synthetic"
EXPECTED_ENV = {
    "HOME": "/workspace",
    "HERMES_HOME": "/workspace/hermes-home",
    "HERMES_CONFIG_HOME": "/workspace/.hermes",
    "PYTHONPATH": "/workspace:/app/src",
    "ASSISTX_URL": "http://127.0.0.1:1",
    "ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN": "SYNTHETIC_BOOTSTRAP_ONLY",
    "ASSISTX_STRICT_EXECUTOR_AUTH": "true",
    "HERMES_AGENT_ID": "hermes-synthetic-canary",
    "HERMES_SELFTASK_ENABLED": "false",
    "HERMES_SELFTASK_BULK_MODELS": "",
    "HERMES_EVAL_PATH": "/workspace/eval.json",
    "HERMES_KNOWLEDGE_ROOT": "/workspace/knowledge",
    "PYTHONDONTWRITEBYTECODE": "1",
}
BANNED_KEYS = {
    "ports", "expose", "secrets", "env_file", "privileged", "devices",
    "volumes_from", "network", "networks", "network_mode_host",
    "extra_hosts", "pid", "ipc", "cgroup", "dns", "dns_search",
    "build", "extends", "depends_on", "configs", "external_links",
}

def safe_fixture(path: Path) -> bool:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        return False
    root = path.parent
    if (root.parent != SCRATCH_PARENT or not ROOT_RE.fullmatch(root.name)
            or root.is_symlink() or root.stat().st_uid != os.getuid()
            or path.stat().st_uid != os.getuid()
            or path.resolve() != path):
        return False
    return True

def verify_render(doc: object, image: str, ro: Path, rw: Path) -> bool:
    if (not IMAGE_RE.fullmatch(image)
            or not safe_fixture(ro) or not safe_fixture(rw)
            or ro == rw or ro.parent != rw.parent
            or not isinstance(doc, dict)):
        return False
    services = doc.get("services")
    if not isinstance(services, dict) or set(services) != {SERVICE}:
        return False
    s = services[SERVICE]
    if not isinstance(s, dict) or BANNED_KEYS.intersection(s):
        return False
    if (s.get("image") != image
            or s.get("pull_policy") != "never"
            or s.get("network_mode") != "none"
            or s.get("read_only") is not True
            or s.get("privileged", False) is not False
            or s.get("user") != "1000:1000"
            or s.get("restart") not in ("no", None)
            or s.get("entrypoint") != ["/entrypoint.sh"]
            or s.get("command") != ["python3", "-B", "-m", "assistx.strict_executor_main"]
            or s.get("profiles") != ["synthetic-canary"]
            or "ALL" not in s.get("cap_drop", [])
            or "no-new-privileges:true" not in s.get("security_opt", [])
            or s.get("environment") != EXPECTED_ENV):
        return False
    v = s.get("volumes")
    if not isinstance(v, list) or len(v) != 2:
        return False
    expected = {
        "/research/knowledge": (str(ro), True),
        "/workspace": (str(rw), False),
    }
    for item in v:
        if not isinstance(item, dict) or item.get("type") != "bind":
            return False
        dest = item.get("target")
        if dest not in expected:
            return False
        source, readonly = expected.pop(dest)
        if item.get("source") != source or item.get("read_only", False) is not readonly:
            return False
        if item.get("bind", {}).get("create_host_path", False):
            return False
    if expected:
        return False
    t = s.get("tmpfs", [])
    if not isinstance(t, list) or not t:
        return False
    return True

def render_once() -> dict:
    result = {
        "mode": "hermes_rootless_compose_render",
        "compose_render_verified": False,
        "production_container_modified": False,
        "production_deployment_authorized": False,
        "real_agent_tasks_executed": 0,
        "provider_requests_forwarded": 0,
        "decision": "HOLD",
        "blockers": [
            "new_candidate_image_source_attestation_missing",
            "normal_main_mock_run_not_verified",
            "production_rw_mounts_still_unsafe",
            "authoritative_task_and_revocation_custody_missing",
        ],
    }
    if os.uname().nodename != "x1-370" or os.getuid() == 0:
        result["blockers"].append("wrong_host_or_uid")
        return result
    SCRATCH_PARENT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="og-hermes-containment-", dir=SCRATCH_PARENT) as temp:
        root = Path(temp)
        ro, rw = root / "knowledge", root / "worktree"
        ro.mkdir(); rw.mkdir()
        image = "sha256:" + "a"*64  # offline placeholder; never pulled or executed
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "HOME": str(Path.home()), "HERMES_CANDIDATE_IMAGE": image,
               "HERMES_SYNTHETIC_KNOWLEDGE": str(ro),
               "HERMES_SYNTHETIC_WORKSPACE": str(rw)}
        cmd = ["docker", "compose", "--env-file", "/dev/null",
               "-f", str(COMPOSE), "--profile", "synthetic-canary",
               "config", "--format", "json"]
        try:
            p = subprocess.run(cmd, text=True, capture_output=True,
                               timeout=20, env=env, check=False)
            if p.returncode or len(p.stdout) > 40000:
                raise ValueError("render failed")
            if not verify_render(json.loads(p.stdout), image, ro, rw):
                raise ValueError("render does not satisfy sandbox contract")
            result["compose_render_verified"] = True
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            result["blockers"].append("compose_render_failed_closed")
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    out = render_once() if args.render_only else {
        "decision": "HOLD", "compose_render_verified": False,
        "production_deployment_authorized": False,
        "production_container_modified": False,
    }
    print(json.dumps(out, indent=2 if args.json else None, sort_keys=True))
    if args.render_only and not out["compose_render_verified"]:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
