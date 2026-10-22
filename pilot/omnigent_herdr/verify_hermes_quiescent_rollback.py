#!/usr/bin/env python3
"""Read-only Hermes rollback safety gate; never stops production.

This is a DENY-ONLY research witness. Legacy Hermes is not a safe rollback
target while broad writable protected-evidence mounts remain present.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from typing import Any

EXPECTED_IMAGE = "sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307"
EXPECTED_CONTAINER = "assistx-hermes-adapter"
EXPECTED_PROTECTED = Path(
    "/media/scott/SSD_4TB/knowledge/50-Research/model-evaluation/free-first-monitor"
)
KNOWN_COMPOSE = Path("/home/scott/git/auto-assist-deploy-20261016/docker-compose.yml")

def synthetic_quiescence(sample: object) -> dict[str, Any]:
    """Positive here only establishes disposable no-worker evidence, NOT a release."""
    errors: set[str] = set()
    required = {
        "schema", "mode", "candidate_absent", "restart_policy",
        "worker_containers", "synthetic_claims", "provider_requests",
        "legacy_restore_requested", "legacy_mounts_restore_requested",
        "private_scratch_retained",
    }
    if not isinstance(sample, dict) or set(sample) != required:
        errors.add("synthetic_quiescence_schema")
    else:
        if type(sample["schema"]) is not int or sample["schema"] != 1:
            errors.add("synthetic_quiescence_schema")
        if sample["mode"] != "disposable_no_dispatch":
            errors.add("incorrect_fallback_mode")
        for field in ("candidate_absent", "private_scratch_retained"):
            if sample[field] is not True:
                errors.add(field + "_not_proven")
        for field in ("legacy_restore_requested", "legacy_mounts_restore_requested"):
            if sample[field] is not False:
                errors.add("unsafe_legacy_restoration")
        if sample["restart_policy"] != "no":
            errors.add("restarting_worker_forbidden")
        for field in ("worker_containers", "synthetic_claims", "provider_requests"):
            if type(sample[field]) is not int or sample[field] != 0:
                errors.add(field + "_not_zero")
    return {
        "schema": 1,
        "mode": "hermes_quiescent_rollback_contract",
        "disposable_quiescence_observed": not errors,
        "violations": sorted(errors),
        "legacy_production_restoration_authorized": False,
        "independent_claim_revocation_verified": False,
        "no_dispatch_production_transition_verified": False,
        "production_rollback_authorized": False,
        "decision": "HOLD",
    }

def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents

def assess_live_snapshot(snapshot: object) -> dict[str, Any]:
    """Assess only structured metadata. Opaque mount sources NEVER printed."""
    errors: set[str] = set()
    overlaps = 0
    unknown = 0
    rw = 0
    n = 0
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "running", "image", "restart_policy", "mounts",
        "compose_build", "compose_readonly", "compose_caps_dropped",
        "compose_mount_count",
    }:
        errors.add("live_metadata_schema")
    else:
        mounts = snapshot["mounts"]
        if not isinstance(mounts, list) or len(mounts) > 128:
            errors.add("live_mount_metadata_untrusted")
            mounts = []
        n = len(mounts)
        try:
            protected = EXPECTED_PROTECTED.resolve(strict=True)
        except (OSError, RuntimeError):
            protected = None
            errors.add("protected_root_unavailable")
        for item in mounts:
            if (not isinstance(item, dict) or
                item.get("Type") != "bind" or
                type(item.get("RW")) is not bool or
                not isinstance(item.get("Source"), str)):
                unknown += 1
                continue
            rw += int(item["RW"])
            try:
                source = Path(item["Source"]).resolve(strict=True)
                if not source.is_dir():
                    unknown += 1
                elif protected is not None and _overlap(source, protected):
                    overlaps += 1
            except (OSError, RuntimeError, ValueError):
                unknown += 1
        if snapshot["running"] is True:
            errors.add("live_legacy_worker_running")
        if snapshot["image"] == EXPECTED_IMAGE:
            errors.add("unsafe_legacy_image_is_rollback_target")
        if snapshot["restart_policy"] != "no":
            errors.add("legacy_auto_restart_enabled")
        if n != 2 or rw > 1:
            errors.add("legacy_broad_mounts")
        if overlaps:
            errors.add("protected_evidence_overlap")
        if unknown:
            errors.add("mount_source_identity_unknown")
        if snapshot["compose_build"] is True:
            errors.add("mutable_build_definition")
        if snapshot["compose_readonly"] is not True:
            errors.add("compose_rootfs_not_readonly")
        if snapshot["compose_caps_dropped"] is not True:
            errors.add("compose_capabilities_not_dropped")
        if type(snapshot["compose_mount_count"]) is not int or snapshot["compose_mount_count"] != 2:
            errors.add("compose_wide_mount_spec")
    return {
        "schema": 1,
        "mode": "hermes_live_rollback_metadata_audit",
        "live_mount_count": n,
        "live_writable_mounts": rw,
        "protected_overlap_count": overlaps,
        "unknown_source_count": unknown,
        "safe_legacy_fallback": False,
        "no_dispatch_production_transition_verified": False,
        "independent_lease_revocation_verified": False,
        "production_rollback_authorized": False,
        "violations": sorted(errors),
        "decision": "HOLD",
    }

def live_readonly() -> dict[str, Any]:
    if os.uname().nodename != "x1-370" or os.geteuid() == 0:
        raise ValueError("nonroot_x1_required")
    proc = subprocess.run(["docker", "inspect", "--type", "container", EXPECTED_CONTAINER],
                          check=False, capture_output=True, text=True, timeout=15)
    if proc.returncode or len(proc.stdout) > 250_000:
        raise ValueError("docker_inspect_unavailable")
    items = json.loads(proc.stdout)
    if not isinstance(items, list) or len(items) != 1:
        raise ValueError("unexpected_inspect_count")
    live = items[0]
    import yaml
    data = yaml.safe_load(KNOWN_COMPOSE.read_text())
    svc = data["services"]["hermes-adapter"]
    return assess_live_snapshot({
        "running": live["State"]["Running"],
        "image": live["Image"],
        "restart_policy": live["HostConfig"]["RestartPolicy"]["Name"],
        "mounts": live["Mounts"],
        "compose_build": bool(svc.get("build")),
        "compose_readonly": svc.get("read_only", False),
        "compose_caps_dropped": "ALL" in (svc.get("cap_drop") or []),
        "compose_mount_count": len(svc.get("volumes") or []),
    })

def default_hold() -> dict[str, Any]:
    return {
        "schema": 1, "mode": "hermes_quiescent_rollback_contract",
        "decision": "HOLD", "production_rollback_authorized": False,
        "no_dispatch_production_transition_verified": False,
        "violations": ["explicit_readonly_audit_not_requested"],
    }

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-live-readonly", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.audit_live_readonly:
        try:
            result = live_readonly()
        except (OSError, ValueError, TypeError, KeyError,
                json.JSONDecodeError, subprocess.TimeoutExpired):
            result = default_hold()
            result["violations"] = ["live_metadata_inspection_failed_closed"]
    else:
        result = default_hold()
    print(json.dumps(result, sort_keys=True, indent=2 if args.json else None))
    if args.audit_live_readonly and "live_metadata_inspection_failed_closed" in result["violations"]:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
