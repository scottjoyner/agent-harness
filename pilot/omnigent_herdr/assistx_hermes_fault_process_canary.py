#!/usr/bin/env python3
"""Deny-only, opt-in Hermes rootless candidate independent fake API process canary."""
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

SITE = Path(__file__).with_name("hermes_fault_sitecustomize.py")
SERVER = Path(__file__).with_name("hermes_fault_api_server.py")
MARKER = "HERMES_INDEPENDENT_FAKE_API_FAULT_PASS"
REQUIRED = (
    "running_image_pinned", "offline_reproducible_overlay",
    "restricted_normal_entrypoint", "separate_fake_http_server",
    "synthetic_real_http_completion", "revoke_and_stale_completion_denied",
    "crash_and_restart_preserve_lease", "expired_takeover_fences_old_token",
    "no_real_agent_or_provider", "no_network_or_production_mounts",
    "all_temporary_docker_objects_removed", "production_unchanged",
)
BLOCKERS = (
    "production_writable_ssd_mounts_unresolved",
    "independent_image_provenance_unverified",
    "real_owner_approved_task_not_executed",
    "independent_authoritative_lease_fencing_unverified",
    "production_change_review_and_rollback_approval_missing",
)

class UnsafeFaultCanary(RuntimeError):
    pass

def require(ok, why):
    if not ok:
        raise UnsafeFaultCanary(why)

def _call(argv, seconds=15):
    return normal._call(argv, seconds=seconds)

def run_physical():
    require(os.uname().nodename == "x1-370" and os.geteuid() != 0, "x1 nonroot only")
    shell.SCRATCH_PARENT.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(SITE.is_file() and SERVER.is_file(), "test source missing")
    normal.sha256_file(SITE)
    normal.sha256_file(SERVER)
    live = shell._image_id()
    require(_call(["docker", "inspect", "--format", "{{.State.Running}}",
                   shell.IMAGE_NAME]).stdout.strip() == "true", "Hermes not running")
    checks = {key: False for key in REQUIRED}
    with normal._exclusive_fixed_base_tag():
        return _run_locked(live, checks)

def _run_locked(live, checks):
    suffix = uuid.uuid4().hex[:16]
    base = normal.STABLE_BASE_TAG
    tags = [normal._safe_tag("overlay-a", suffix), normal._safe_tag("overlay-b", suffix)]
    name = "og-hermes-normal-test-" + suffix
    created = False
    created_tags = []
    candidate_id = None
    with tempfile.TemporaryDirectory(
        dir=shell.SCRATCH_PARENT, prefix="og-hermes-normal-fixture-"
    ) as temp:
        root = Path(temp)
        require(root.parent == shell.SCRATCH_PARENT and
                normal.ROOT_RE.fullmatch(root.name), "wrong scratch root")
        root.chmod(0o755)
        knowledge, workspace = root / "knowledge", root / "workspace"
        knowledge.mkdir()
        workspace.mkdir()
        (knowledge / "synthetic-source.txt").write_text("SYNTHETIC_ONLY\n")
        (knowledge / "synthetic-source.txt").chmod(0o644)
        for src, target in ((SITE, workspace / "sitecustomize.py"),
                            (SERVER, workspace / "hermes_fault_api_server.py")):
            shutil.copyfile(src, target)
            target.chmod(0o644)
            require(normal.sha256_file(src) == normal.sha256_file(target),
                    "fixture changed in staging")
        context = normal._dockerfile_context(root)
        require(all(normal._absent_docker_object("image", tag) for tag in tags),
                "disposable tag occupied")
        try:
            created_tags.append(base)
            require(_call(["docker", "tag", live, base]).returncode == 0,
                    "base pin failed")
            env = {**os.environ, "SOURCE_DATE_EPOCH": normal.SOURCE_DATE_EPOCH,
                   "DOCKER_BUILDKIT": "1"}
            ids = []
            for tag in tags:
                created_tags.append(tag)
                result = normal._call(normal._build_argv(context, base, tag),
                                      seconds=90, env=env)
                require(result.returncode == 0, "offline overlay build failed")
                inspect = _call(["docker", "image", "inspect", "--format",
                                 "{{.Id}}", tag])
                require(inspect.returncode == 0 and
                        shell.IMAGE_ID_RE.fullmatch(inspect.stdout.strip()),
                        "bad overlay image")
                ids.append(inspect.stdout.strip())
            require(ids[0] == ids[1], "two overlay images differ")
            candidate_id = ids[0]
            checks["offline_reproducible_overlay"] = True
            checks["running_image_pinned"] = True
            require(_call(normal._container_argv(candidate_id, name, knowledge,
                                                 workspace)).returncode == 0,
                    "cannot create test container")
            created = True
            normal._inspect(name, candidate_id, knowledge, workspace)
            checks["restricted_normal_entrypoint"] = True
            checks["no_network_or_production_mounts"] = True
            started = _call(["docker", "start", "--attach", name], 70)
            exit_state = _call(["docker", "inspect", "--format", "{{.State.ExitCode}}",
                                name])
            if started.returncode != 0:
                raise UnsafeFaultCanary("disposable worker exit " +
                                        started.stderr[-1200:])
            require(started.stdout.splitlines().count(MARKER) == 1 and
                    exit_state.returncode == 0 and exit_state.stdout.strip() == "0",
                    "fake independent HTTP process worker failed")
            evidence = json.loads((workspace / "fault-result.json").read_text())
            expected_events = [
                "real_http_done", "revoked_stale_denied", "server_crash_detected",
                "restart_preserves_live_lease", "expired_lease_reclaimed_old_fenced"]
            require(evidence.get("events") == expected_events,
                    "fake process negative event sequence mismatch")
            require(evidence.get("separate_server_pid") is True and
                    evidence.get("server_starts") == 3, "API process not isolated")
            checks["separate_fake_http_server"] = True
            checks["synthetic_real_http_completion"] = True
            checks["revoke_and_stale_completion_denied"] = True
            checks["crash_and_restart_preserve_lease"] = True
            checks["expired_takeover_fences_old_token"] = True
            require(evidence.get("fake_engine_runs") == 1 and
                    evidence.get("provider_requests") == 0 and
                    evidence.get("real_api_tasks") == 0 and
                    not list(workspace.rglob(".env")), "real task/credential detected")
            checks["no_real_agent_or_provider"] = True
            live_post = shell._image_id()
            running = _call(["docker", "inspect", "--format", "{{.State.Running}}",
                             shell.IMAGE_NAME])
            checks["production_unchanged"] = (
                live_post == live and running.stdout.strip() == "true")
        finally:
            if created:
                _call(["docker", "rm", "--force", name], 15)
            for tag in reversed(created_tags):
                _call(["docker", "image", "rm", tag], 15)
            checks["all_temporary_docker_objects_removed"] = (
                normal._absent_docker_object("container", name)
                and all(normal._absent_docker_object("image", tag)
                        for tag in (base, *tags)))
    require(all(checks.values()), "physical checks or cleanup incomplete")
    return checks, live, candidate_id

def capture(physical=False):
    report = {
        "schema": 1, "mode": "hermes_separate_local_fake_api_crash_lease",
        "host": "x1-370", "physical_canary_executed": False,
        "exact_running_image": None, "overlay_image": None,
        **{key: False for key in REQUIRED},
        "provider_requests_forwarded": 0, "real_tasks_executed": 0,
        "production_deployment_authorized": False,
        "decision": "HOLD", "blockers": list(BLOCKERS),
    }
    if physical:
        try:
            checks, live, overlay = run_physical()
            report.update(checks)
            report.update(exact_running_image=live, overlay_image=overlay,
                          physical_canary_executed=all(checks.values()))
        except (UnsafeFaultCanary, normal.UnsafeNormalStartup,
                shell.UnsafeCanary, ValueError, OSError,
                subprocess.TimeoutExpired) as exc:
            report["blockers"].append("disposable_fake_api_process_failed_closed")
            # Synthetic-only error reported locally, not a release receipt.
            print("FAULT_CANARY_ERROR", str(exc)[-350:], flush=True)
    return report

def validate(report):
    expected = capture()
    if not isinstance(report, dict) or set(report) != set(expected):
        return False
    if any(type(report[key]) is not bool for key in REQUIRED):
        return False
    flag = report["physical_canary_executed"]
    if type(flag) is not bool or flag != all(report[k] for k in REQUIRED):
        return False
    for field in ("exact_running_image", "overlay_image"):
        value = report[field]
        if value is not None and (not isinstance(value, str)
                                  or not shell.IMAGE_ID_RE.fullmatch(value)):
            return False
    if flag and (not report["exact_running_image"] or not report["overlay_image"]):
        return False
    for key in ("schema", "mode", "host", "provider_requests_forwarded",
                "real_tasks_executed", "production_deployment_authorized", "decision"):
        if type(report[key]) is not type(expected[key]) or report[key] != expected[key]:
            return False
    return (isinstance(report["blockers"], list)
            and all(isinstance(item, str) for item in report["blockers"])
            and all(item in report["blockers"] for item in BLOCKERS))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-disposable-fault", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    record = capture(physical=args.physical_disposable_fault)
    if not validate(record):
        raise SystemExit("invalid fault qualification report")
    print(json.dumps(record, indent=2 if args.json else None, sort_keys=True))
    if args.physical_disposable_fault and not record["physical_canary_executed"]:
        raise SystemExit(2)
