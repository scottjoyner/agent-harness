#!/usr/bin/env python3
"""Read-only x1 Hermes runtime/image/source parity witness; never deploys."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

HOST = "x1-370"
CONTAINER = "assistx-hermes-adapter"
SOURCE = Path("/home/scott/git/auto-assist-deploy-20261016")
FILES = {
    "/entrypoint.sh": "entrypoint.sh",
    "/app/src/assistx/strict_executor_main.py": "src/assistx/strict_executor_main.py",
    "/app/src/assistx/strict_executor_adapter.py": "src/assistx/strict_executor_adapter.py",
    "/app/src/assistx/agents/hermes_agent_adapter.py":
        "src/assistx/agents/hermes_agent_adapter.py",
}
IMAGE_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
BLOCKERS = [
    "build_source_commit_not_independently_attested",
    "image_build_chain_and_signer_custody_unverified",
    "production_source_bind_remains_writable",
    "production_hermes_evidence_rw_aliases_remain",
    "source_owner_execution_revocation_unverified",
]

def command(argv: list[str], timeout=10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True,
                          check=False, timeout=timeout)

def image_hash_command(image_id: str) -> list[str]:
    if not IMAGE_RE.fullmatch(image_id):
        raise ValueError("untrusted image object ID")
    return [
        "docker", "run", "--rm", "--pull=never", "--network=none",
        "--read-only", "--user=1000:1000", "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--pids-limit=16", "--memory=128m", "--cpus=0.25",
        "--entrypoint=/usr/bin/sha256sum",
        image_id, *FILES.keys(),
    ]

def parse_image_hashes(data: str) -> dict[str, str]:
    if len(data) > 3000:
        raise ValueError("unbounded hash output")
    pairs: dict[str, str] = {}
    lines = data.splitlines()
    if len(lines) != len(FILES):
        raise ValueError("unexpected image file count")
    for line in lines:
        # GNU sha256sum uses exactly two spaces before its filename.
        parts = line.split("  ", 1)
        if (len(parts) != 2 or not DIGEST_RE.fullmatch(parts[0])
                or parts[1] not in FILES or parts[1] in pairs):
            raise ValueError("untrusted image hash record")
        pairs[parts[1]] = parts[0]
    if set(pairs) != set(FILES):
        raise ValueError("missing required image hash")
    return pairs

def inspect_mounts(mounts: object) -> bool:
    if not isinstance(mounts, list):
        return False
    selected = [x for x in mounts if isinstance(x, dict)
                and x.get("Destination") == "/app/src"]
    if len(selected) != 1:
        return False
    mount = selected[0]
    # The current deployment has a RW live source bind.  This is parity
    # evidence only: production hardening cannot be inferred from it.
    try:
        actual = Path(mount["Source"]).resolve(strict=True)
        expected = (SOURCE / "src").resolve(strict=True)
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return (mount.get("Type") == "bind"
            and mount.get("RW") is True
            and actual == expected)

def local_hashes() -> dict[str, str]:
    values = {}
    for inside, relative in FILES.items():
        p = SOURCE / relative
        if p.is_symlink() or not p.is_file():
            raise ValueError("source file absent/symlink")
        if not p.resolve(strict=True).is_relative_to(SOURCE.resolve(strict=True)):
            raise ValueError("source escapes known checkout")
        if p.stat().st_size > 1024 * 1024:
            raise ValueError("unexpected source file size")
        values[inside] = hashlib.sha256(p.read_bytes()).hexdigest()
    return values

def capture(physical: bool = False) -> dict:
    result = {
        "schema": 1,
        "mode": "assistx_hermes_readonly_four_file_parity",
        "host": HOST,
        "physical_readonly_check_executed": False,
        "image_object_id": None,
        "local_checkout_commit": None,
        "files_checked": 0,
        "four_critical_file_hashes_match": False,
        "live_source_bind_verified_rw": False,
        "source_commit_independently_attested": False,
        "production_source_mount_readonly": False,
        "source_owner_execution_authorized": False,
        "production_deployment_authorized": False,
        "production_containers_modified": False,
        "provider_requests_forwarded": 0,
        "decision": "HOLD",
        "blockers": BLOCKERS.copy(),
    }
    if not physical:
        return result
    try:
        if os.uname().nodename != HOST or os.getuid() == 0:
            raise ValueError("wrong host/uid")
        first = command(["docker", "inspect", "--format",
                         "{{.Image}}", CONTAINER])
        image = first.stdout.strip()
        if first.returncode or not IMAGE_RE.fullmatch(image):
            raise ValueError("running Hermes image unavailable")
        result["image_object_id"] = image

        mounts_cmd = command(["docker", "inspect", "--format",
                              "{{json .Mounts}}", CONTAINER])
        if mounts_cmd.returncode or len(mounts_cmd.stdout) > 30000:
            raise ValueError("container mount inspection failed")
        if not inspect_mounts(json.loads(mounts_cmd.stdout)):
            raise ValueError("unexpected current live source bind")
        result["live_source_bind_verified_rw"] = True

        git = command(["git", "-C", str(SOURCE), "rev-parse", "HEAD"])
        commit = git.stdout.strip()
        if git.returncode or not COMMIT_RE.fullmatch(commit):
            raise ValueError("local source commit unavailable")
        clean = command(["git", "-C", str(SOURCE), "status",
                         "--porcelain", "--untracked-files=no"])
        if clean.returncode or clean.stdout:
            raise ValueError("source checkout has tracked modifications")
        result["local_checkout_commit"] = commit
        expected_hashes = local_hashes()
        before = command(["git", "-C", str(SOURCE), "rev-parse", "HEAD"])
        actual = command(image_hash_command(image), timeout=25)
        if actual.returncode:
            raise ValueError("isolated image hash failed")
        image_hashes = parse_image_hashes(actual.stdout)
        if image_hashes != expected_hashes:
            raise ValueError("live image/source code does not match")
        after = command(["docker", "inspect", "--format",
                         "{{.Image}}", CONTAINER])
        source_after = command(["git", "-C", str(SOURCE), "rev-parse", "HEAD"])
        if (after.returncode or after.stdout.strip() != image
                or before.returncode or before.stdout.strip() != commit
                or source_after.returncode or source_after.stdout.strip() != commit
                or local_hashes() != expected_hashes):
            raise ValueError("source or image drifted during audit")
        result["physical_readonly_check_executed"] = True
        result["files_checked"] = len(FILES)
        result["four_critical_file_hashes_match"] = True
    except (OSError, TypeError, KeyError, ValueError,
            subprocess.TimeoutExpired, json.JSONDecodeError):
        result["blockers"].append("physical_parity_check_failed_closed")
        # Do not report partial booleans as authoritative physical pass.
        result["physical_readonly_check_executed"] = False
        result["four_critical_file_hashes_match"] = False
        result["live_source_bind_verified_rw"] = False
        result["files_checked"] = 0
    return result

def verify(data: object) -> bool:
    default = capture()
    if not isinstance(data, dict) or set(data) != set(default):
        return False
    immutable = (
        "schema", "mode", "host", "source_commit_independently_attested",
        "production_source_mount_readonly", "source_owner_execution_authorized",
        "production_deployment_authorized", "production_containers_modified",
        "provider_requests_forwarded", "decision",
    )
    if any(type(data[k]) is not type(default[k]) or
           data[k] != default[k] for k in immutable):
        return False
    for key in ("physical_readonly_check_executed",
                "four_critical_file_hashes_match",
                "live_source_bind_verified_rw"):
        if type(data[key]) is not bool:
            return False
    if not isinstance(data["blockers"], list) or not all(
            x in data["blockers"] for x in BLOCKERS):
        return False
    image, commit = data["image_object_id"], data["local_checkout_commit"]
    if image is not None and (not isinstance(image, str)
                              or not IMAGE_RE.fullmatch(image)):
        return False
    if commit is not None and (not isinstance(commit, str)
                               or not COMMIT_RE.fullmatch(commit)):
        return False
    if type(data["files_checked"]) is not int:
        return False
    if data["physical_readonly_check_executed"]:
        return (image is not None and commit is not None
                and data["files_checked"] == len(FILES)
                and data["four_critical_file_hashes_match"] is True
                and data["live_source_bind_verified_rw"] is True)
    return (data["files_checked"] == 0
            and data["four_critical_file_hashes_match"] is False
            and data["live_source_bind_verified_rw"] is False)

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--physical-readonly-provenance", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    outcome = capture(args.physical_readonly_provenance)
    if not verify(outcome):
        raise SystemExit("unsafe provenance report")
    print(json.dumps(outcome, sort_keys=True, indent=2 if args.json else None))
    if args.physical_readonly_provenance and not outcome[
            "physical_readonly_check_executed"]:
        raise SystemExit(2)
