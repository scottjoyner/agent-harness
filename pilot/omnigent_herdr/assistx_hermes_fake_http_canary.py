#!/usr/bin/env python3
"""Opt-in networkless real-HTTP Hermes canary using a disposable rootless image.

Reuse the exact running image + checked-in candidate entrypoint but never
touch Compose, production mounts, secrets, workers, billing or live tasks.
A loopback HTTP fixture exercises synthetic claim/lease/restart behavior.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
import assistx_hermes_container_canary as shell
import assistx_hermes_normal_startup_canary as normal

SITE = Path(__file__).with_name("hermes_fake_http_sitecustomize.py")
CHECKS = (
    "exact_image_pinned",
    "two_offline_overlay_builds_identical",
    "normal_entrypoint_static_confinement",
    "real_http_claim_heartbeat_completion",
    "http_lease_revocation_denies_completion",
    "restart_does_not_redeliver_completed_task",
    "no_real_provider_or_agent_executed",
    "no_production_mount_or_network",
    "all_disposable_objects_removed",
    "production_hermes_unchanged",
)
BLOCKERS = (
    "production_hermes_broad_writable_mounts",
    "independent_image_provenance_absent",
    "real_source_owner_claim_authorization_not_verified",
    "real_multi_agent_fencing_revocation_not_verified",
    "independent_review_and_approval_absent",
)

class UnsafeHttpCanary(RuntimeError):
    pass

def _must(success: bool, explanation: str) -> None:
    if not success:
        raise UnsafeHttpCanary(explanation)

def _call(argv: list[str], seconds: int = 20):
    return normal._call(argv, seconds=seconds)

def _live_running_id() -> str:
    obj = shell._image_id()
    _must(bool(shell.IMAGE_ID_RE.fullmatch(obj)), "invalid live image ID")
    state = _call(["docker", "inspect", "--format", "{{.State.Running}}",
                   shell.IMAGE_NAME], 7)
    _must(state.returncode == 0 and state.stdout.strip() == "true",
          "production Hermes is not running")
    return obj

def _run() -> tuple[dict, str, str]:
    checks = {key: False for key in CHECKS}
    live = _live_running_id()
    _must(os.uname().nodename == "x1-370" and os.geteuid() != 0,
          "isolated physical canary is x1-only/nonroot")
    _must(SITE.is_file() and not SITE.is_symlink(), "test fixture not a real file")
    normal.sha256_file(SITE)
    _must(not normal.STABLE_BASE_LOCK.exists(),
          "another normal overlay build is running")
    with normal._exclusive_fixed_base_tag():
        return _run_locked(live, checks)

def _run_locked(live: str, checks: dict) -> tuple[dict, str, str]:
    suffix = uuid.uuid4().hex[:16]
    base = normal.STABLE_BASE_TAG
    overlays = [normal._safe_tag("overlay-a", suffix),
                normal._safe_tag("overlay-b", suffix)]
    name = "og-hermes-normal-test-" + suffix
    _must(normal.CONTAINER_RE.fullmatch(name) is not None, "name invalid")
    tags = []
    created = False
    overlay_id = ""
    with tempfile.TemporaryDirectory(
        prefix="og-hermes-normal-fixture-", dir=shell.SCRATCH_PARENT
    ) as temp:
        root = Path(temp)
        _must(root.parent == shell.SCRATCH_PARENT and
              normal.ROOT_RE.fullmatch(root.name) is not None,
              "synthetic scratch boundary invalid")
        root.chmod(0o755)
        knowledge, workspace = root / "knowledge", root / "workspace"
        knowledge.mkdir(mode=0o755)
        workspace.mkdir(mode=0o755)
        (knowledge / "synthetic-source.txt").write_text("SYNTHETIC_ONLY\n")
        (knowledge / "synthetic-source.txt").chmod(0o644)
        shutil.copyfile(SITE, workspace / "sitecustomize.py")
        (workspace / "sitecustomize.py").chmod(0o644)
        _must(normal.sha256_file(SITE) == normal.sha256_file(workspace / "sitecustomize.py"),
              "synthetic fixture differs")
        ctx = normal._dockerfile_context(root)
        _must(all(normal._absent_docker_object("image", t) for t in overlays),
              "disposable image tag collision")
        try:
            tags.append(base)
            _must(_call(["docker", "tag", live, base], 10).returncode == 0,
                  "exact local base tag failed")
            ids = []
            env = {**os.environ, "SOURCE_DATE_EPOCH": normal.SOURCE_DATE_EPOCH,
                   "DOCKER_BUILDKIT": "1"}
            for tag in overlays:
                tags.append(tag)
                build = normal._call(normal._build_argv(ctx, base, tag),
                                     seconds=90, env=env)
                _must(build.returncode == 0, "disposable offline build failed")
                obj = _call(["docker", "image", "inspect", "--format", "{{.Id}}", tag], 7)
                _must(obj.returncode == 0 and
                      shell.IMAGE_ID_RE.fullmatch(obj.stdout.strip()) is not None,
                      "overlay object identity invalid")
                ids.append(obj.stdout.strip())
            _must(len(ids) == 2 and ids[0] == ids[1], "overlay builds mismatch")
            overlay_id = ids[0]
            checks["two_offline_overlay_builds_identical"] = True
            checks["exact_image_pinned"] = True
            _must(_call(normal._container_argv(overlay_id, name, knowledge,
                                               workspace), 20).returncode == 0,
                  "disposable container create failed")
            created = True
            normal._inspect(name, overlay_id, knowledge, workspace)
            checks["normal_entrypoint_static_confinement"] = True
            checks["no_production_mount_or_network"] = True
            for stage, marker in (
                (1, "ROOTLESS_HERMES_HTTP_FIRST_PASS"),
                (2, "ROOTLESS_HERMES_HTTP_RESTART_PASS"),
            ):
                result = _call(["docker", "start", "--attach", name], 50)
                exit_state = _call(["docker", "inspect", "--format",
                                    "{{.State.ExitCode}}", name], 7)
                _must(result.returncode == 0 and
                      result.stdout.splitlines().count(marker) == 1 and
                      exit_state.returncode == 0 and
                      exit_state.stdout.strip() == "0",
                      "normal entrypoint synthetic HTTP startup failed")
                evidence = json.loads((workspace / f"http-stage-{stage}.json").read_text())
                _must(evidence["real_http"] is True and
                      evidence["restart"] is (stage == 2),
                      "incorrect HTTP fixture stage evidence")
                if stage == 1:
                    ev = evidence["events"]
                    _must(ev[:8] == [
                        "poll", "claim:synthetic-http-task-001",
                        "token:synthetic-http-task-001", "heartbeat:main",
                        "context", "heartbeat:main", "memory", "complete:main"],
                        "claim/heartbeat/completion trace mismatch")
                    _must("heartbeat:revoked" in ev and
                          ev.count("complete:revoked_denied") == 1,
                          "synthetic revocation negative path missing")
                    checks["real_http_claim_heartbeat_completion"] = True
                    checks["http_lease_revocation_denies_completion"] = True
                else:
                    _must(evidence["events"] == ["poll"],
                          "completed task was dispatched following restart")
                    checks["restart_does_not_redeliver_completed_task"] = True
            _must(not list(workspace.rglob(".env")), "unexpected credential file")
            _must((workspace / "hermes-home/config.yaml").is_file(),
                  "rootless config missing")
            checks["no_real_provider_or_agent_executed"] = True
            checks["production_hermes_unchanged"] = _live_running_id() == live
            _must(checks["production_hermes_unchanged"],
                  "production Hermes image or run state changed")
        finally:
            if created:
                _call(["docker", "rm", "--force", name], 15)
            for tag in reversed(tags):
                _call(["docker", "image", "rm", tag], 15)
            checks["all_disposable_objects_removed"] = (
                normal._absent_docker_object("container", name)
                and all(normal._absent_docker_object("image", t)
                        for t in (base, *overlays))
            )
    _must(all(checks.values()), "physical fixture or cleanup checks incomplete")
    return checks, live, overlay_id

def capture(physical: bool = False) -> dict:
    result = {
        "schema": 1,
        "mode": "hermes_rootless_loopback_http_restart",
        "host": "x1-370",
        "physical_canary_executed": False,
        "exact_running_image": None,
        "reproducible_overlay_image": None,
        **{key: False for key in CHECKS},
        "provider_requests_forwarded": 0,
        "real_agent_tasks_executed": 0,
        "production_deployment_authorized": False,
        "decision": "HOLD",
        "blockers": list(BLOCKERS),
    }
    if not physical:
        return result
    try:
        shell.SCRATCH_PARENT.mkdir(parents=True, mode=0o700, exist_ok=True)
        checks, live, overlay = _run()
        result.update(checks)
        result["exact_running_image"] = live
        result["reproducible_overlay_image"] = overlay
        result["physical_canary_executed"] = all(checks.values())
    except (UnsafeHttpCanary, normal.UnsafeNormalStartup,
            shell.UnsafeCanary, OSError, ValueError, TypeError,
            subprocess.TimeoutExpired):
        result["blockers"].append("physical_http_canary_failed_closed")
    return result

def validate(record: object) -> bool:
    expected = capture()
    if not isinstance(record, dict) or set(record) != set(expected):
        return False
    if any(type(record[key]) is not bool for key in CHECKS):
        return False
    if type(record["physical_canary_executed"]) is not bool:
        return False
    if record["physical_canary_executed"] != all(record[k] for k in CHECKS):
        return False
    for key in ("exact_running_image", "reproducible_overlay_image"):
        value = record[key]
        if value is not None and (not isinstance(value, str)
                                  or shell.IMAGE_ID_RE.fullmatch(value) is None):
            return False
    if record["physical_canary_executed"] and not (
        record["exact_running_image"] and record["reproducible_overlay_image"]):
        return False
    for key in ("schema", "mode", "host", "provider_requests_forwarded",
                "real_agent_tasks_executed", "production_deployment_authorized",
                "decision"):
        if type(record[key]) is not type(expected[key]) or record[key] != expected[key]:
            return False
    return isinstance(record["blockers"], list) and all(
        item in record["blockers"] for item in BLOCKERS
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-disposable-http-restart", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = capture(physical=args.physical_disposable_http_restart)
    if not validate(report):
        raise SystemExit("invalid Hermes HTTP canary report")
    print(json.dumps(report, sort_keys=True, indent=2 if args.json else None))
    if args.physical_disposable_http_restart and not report["physical_canary_executed"]:
        raise SystemExit(2)
