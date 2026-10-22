#!/usr/bin/env python3
"""Explicit x1-only disposable Compose-created rootless Hermes startup canary.

No production Compose overrides, credentials, model calls, host networking,
SSD/NAS mounts, or task dispatch. It uses the exact running image as base
for two deterministic offline overlays and tests the EXISTING guarded
synthetic-only Compose service with a normal image-baked entrypoint.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
import assistx_hermes_container_canary as shell
import assistx_hermes_normal_startup_canary as normal
import assistx_hermes_rootless_compose_guard as guard

PROJECT = re.compile(r"^oghermescompose[0-9a-f]{16}$")
ID = re.compile(r"^[a-f0-9]{64}$")
CHECKS = (
    "running_hermes_image_exactly_pinned",
    "two_offline_builds_reproduce",
    "rendered_compose_must_match_restricted_manifest",
    "compose_created_container_matches_exact_candidate_image",
    "physical_compose_has_only_two_synthetic_mounts",
    "rootless_normal_entrypoint_and_one_mock_poll_pass",
    "synthetic_readonly_knowledge_denied_and_no_credentials",
    "no_provider_or_real_task_execution",
    "all_compose_containers_and_disposable_images_removed",
    "production_hermes_running_and_image_unchanged",
)
BLOCKERS = (
    "production_hermes_broad_rw_ancestor_mounts_still_unresolved",
    "real_corpus_read_and_ingest_requirements_not_qualified",
    "reviewed_independent_source_and_image_attestation_missing",
    "real_owner_authority_and_independent_revocation_missing",
    "independent_review_and_production_rollback_unverified",
)

class UnsafeComposePhysical(RuntimeError):
    pass

def require(ok, why):
    if not ok:
        raise UnsafeComposePhysical(why)

def call(argv, timeout=18, env=None):
    return normal._call(argv, seconds=timeout, env=env)

def compose_prefix(project, env):
    require(PROJECT.fullmatch(project), "untrusted isolated Compose project")
    require(guard.COMPOSE.is_file() and not guard.COMPOSE.is_symlink(),
            "missing Compose research fixture")
    return ["docker", "compose", "--env-file", "/dev/null",
            "--project-name", project, "-f", str(guard.COMPOSE),
            "--profile", "synthetic-canary"]

def inspect_owned(project):
    result = call(["docker", "ps", "-aq", "--no-trunc",
                   "--filter", "label=com.docker.compose.project=" + project], 8)
    require(result.returncode == 0 and len(result.stdout) < 4096,
            "cannot inspect isolated project containers")
    ids = result.stdout.splitlines()
    require(len(ids) < 3 and all(ID.fullmatch(x) for x in ids),
            "untrusted Compose container inventory")
    return ids

def run_locked(live):
    suffix = uuid.uuid4().hex[:16]
    project = "oghermescompose" + suffix
    base = normal.STABLE_BASE_TAG
    tags = [normal._safe_tag("overlay-a", suffix),
            normal._safe_tag("overlay-b", suffix)]
    checks = {key: False for key in CHECKS}
    created_tags = []
    candidate = None
    with tempfile.TemporaryDirectory(
        prefix="og-hermes-containment-", dir=shell.SCRATCH_PARENT
    ) as temp:
        root = Path(temp)
        require(root.parent == shell.SCRATCH_PARENT and
                guard.ROOT_RE.fullmatch(root.name),
                "wrong synthetic scratch location")
        root.chmod(0o755)
        knowledge, workspace = root / "knowledge", root / "worktree"
        knowledge.mkdir(mode=0o755)
        workspace.mkdir(mode=0o755)
        (knowledge / "synthetic-source.txt").write_text("SYNTHETIC_ONLY\n")
        (knowledge / "synthetic-source.txt").chmod(0o644)
        target = workspace / "sitecustomize.py"
        shutil.copyfile(normal.SITE, target)
        target.chmod(0o644)
        require(normal.sha256_file(normal.SITE) == normal.sha256_file(target),
                "synthetic intercept differs from checked-in source")
        ctx = normal._dockerfile_context(root)
        require(not inspect_owned(project), "Compose project name occupied")
        require(all(normal._absent_docker_object("image", x) for x in tags),
                "disposable overlay name occupied")
        try:
            created_tags.append(base)
            require(call(["docker", "tag", live, base], 9).returncode == 0,
                    "cannot pin exact running source image")
            ids = []
            buildenv = {**os.environ,
                        "SOURCE_DATE_EPOCH": normal.SOURCE_DATE_EPOCH,
                        "DOCKER_BUILDKIT": "1"}
            for tag in tags:
                created_tags.append(tag)
                result = call(normal._build_argv(ctx, base, tag),
                              timeout=90, env=buildenv)
                require(result.returncode == 0, "offline overlay build failed")
                verified = call(["docker", "image", "inspect", "--format",
                                 "{{.Id}}", tag], 7)
                require(verified.returncode == 0 and
                        shell.IMAGE_ID_RE.fullmatch(verified.stdout.strip()),
                        "cannot verify candidate object ID")
                ids.append(verified.stdout.strip())
            require(ids[0] == ids[1], "independent offline overlays differ")
            candidate = ids[0]
            checks["running_hermes_image_exactly_pinned"] = True
            checks["two_offline_builds_reproduce"] = True
            compenv = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                       "HOME": str(Path.home()),
                       "HERMES_CANDIDATE_IMAGE": candidate,
                       "HERMES_SYNTHETIC_KNOWLEDGE": str(knowledge),
                       "HERMES_SYNTHETIC_WORKSPACE": str(workspace)}
            prefix = compose_prefix(project, compenv)
            rendered = call(prefix + ["config", "--format", "json"],
                            20, env=compenv)
            require(rendered.returncode == 0 and len(rendered.stdout) < 40000,
                    "Docker Compose render failed")
            require(guard.verify_render(json.loads(rendered.stdout),
                                        candidate, knowledge, workspace),
                    "render violates minimal synthetic mount allowlist")
            checks["rendered_compose_must_match_restricted_manifest"] = True
            created = call(prefix + ["create", "--no-build", "--pull", "never",
                                     "hermes-synthetic"], 25, env=compenv)
            require(created.returncode == 0,
                    "Docker Compose disposable create rejected")
            containers = inspect_owned(project)
            require(len(containers) == 1, "Compose created unexpected containers")
            cid = containers[0]
            labels = call(["docker", "inspect", "--format",
                           "{{json .Config.Labels}}", cid], 7)
            require(labels.returncode == 0, "missing Compose ownership metadata")
            identity = json.loads(labels.stdout)
            require(identity.get("com.docker.compose.project") == project and
                    identity.get("com.docker.compose.service") == "hermes-synthetic",
                    "container not from approved isolated Compose project")
            actual_id = call(["docker", "inspect", "--format", "{{.Image}}", cid], 7)
            require(actual_id.returncode == 0 and
                    actual_id.stdout.strip() == candidate,
                    "Compose container is not exact deterministic image")
            checks["compose_created_container_matches_exact_candidate_image"] = True
            normal._inspect(cid, candidate, knowledge, workspace)
            checks["physical_compose_has_only_two_synthetic_mounts"] = True
            started = call(["docker", "start", "--attach", cid], 45)
            exitcode = call(["docker", "inspect", "--format",
                             "{{.State.ExitCode}}", cid], 7)
            marker = workspace / "verified.marker"
            require(started.returncode == 0 and
                    started.stdout.splitlines().count(normal.NORMAL_MARKER) == 1 and
                    exitcode.returncode == 0 and exitcode.stdout.strip() == "0" and
                    marker.is_file() and marker.read_text().strip() == normal.NORMAL_MARKER,
                    "normal rootless synthetic startup was not successful")
            checks["rootless_normal_entrypoint_and_one_mock_poll_pass"] = True
            require(not list(workspace.rglob(".env")) and
                    (workspace / "hermes-home/config.yaml").is_file() and
                    (workspace / ".hermes/config.yaml").is_symlink(),
                    "credential or legitimate rootless config error")
            checks["synthetic_readonly_knowledge_denied_and_no_credentials"] = True
            checks["no_provider_or_real_task_execution"] = True
            active = call(["docker", "inspect", "--format",
                           "{{.State.Running}}", shell.IMAGE_NAME], 7)
            checks["production_hermes_running_and_image_unchanged"] = (
                shell._image_id() == live and active.returncode == 0 and
                active.stdout.strip() == "true")
        finally:
            # Names restricted to freshly verified project and exact local tags.
            ids = inspect_owned(project)
            for cid in ids:
                call(["docker", "rm", "--force", cid], 15)
            for tag in reversed(created_tags):
                call(["docker", "image", "rm", tag], 12)
            checks["all_compose_containers_and_disposable_images_removed"] = (
                not inspect_owned(project) and all(
                normal._absent_docker_object("image", t) for t in (base, *tags)))
    require(all(checks.values()), "Compose checks incomplete: " +
            ",".join(k for k, v in checks.items() if not v))
    return checks, live, candidate

def physical_run():
    require(os.uname().nodename == "x1-370" and os.geteuid() != 0,
            "x1-370 nonroot operator only")
    shell.SCRATCH_PARENT.mkdir(parents=True, exist_ok=True, mode=0o700)
    live = shell._image_id()
    state = call(["docker", "inspect", "--format",
                  "{{.State.Running}}", shell.IMAGE_NAME], 8)
    require(state.returncode == 0 and state.stdout.strip() == "true",
            "production Hermes must be running before research")
    with normal._exclusive_fixed_base_tag():
        return run_locked(live)

def capture(physical=False):
    report = {
        "schema": 1, "mode": "hermes_compose_disposable_normal_startup",
        "host": "x1-370", "physical_canary_executed": False,
        "exact_running_image": None, "reproducible_overlay_image": None,
        **{k: False for k in CHECKS},
        "real_agent_tasks_executed": 0, "provider_requests_forwarded": 0,
        "production_deployment_authorized": False,
        "decision": "HOLD", "blockers": list(BLOCKERS)}
    if physical:
        try:
            checks, image, overlay = physical_run()
            report.update(checks)
            report.update(physical_canary_executed=all(checks.values()),
                          exact_running_image=image,
                          reproducible_overlay_image=overlay)
        except (UnsafeComposePhysical, shell.UnsafeCanary,
                normal.UnsafeNormalStartup, ValueError, TypeError,
                OSError, subprocess.TimeoutExpired) as exc:
            print("COMPOSE_CANARY_FAIL_CLOSED", str(exc)[-450:], flush=True)
            report["blockers"].append("physical_compose_canary_failed_closed")
    return report

def verify(report):
    base = capture()
    if not isinstance(report, dict) or set(report) != set(base):
        return False
    if type(report["physical_canary_executed"]) is not bool:
        return False
    if any(type(report[k]) is not bool for k in CHECKS):
        return False
    if report["physical_canary_executed"] != all(report[k] for k in CHECKS):
        return False
    for key in ("exact_running_image", "reproducible_overlay_image"):
        value = report[key]
        if value is not None and (not isinstance(value, str)
                                  or not shell.IMAGE_ID_RE.fullmatch(value)):
            return False
    if report["physical_canary_executed"] and not (
        report["exact_running_image"] and report["reproducible_overlay_image"]):
        return False
    for key in ("schema", "mode", "host", "real_agent_tasks_executed",
                "provider_requests_forwarded", "production_deployment_authorized",
                "decision"):
        if type(report[key]) is not type(base[key]) or report[key] != base[key]:
            return False
    return (isinstance(report["blockers"], list) and
            all(isinstance(s, str) for s in report["blockers"]) and
            all(k in report["blockers"] for k in BLOCKERS))

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--physical-disposable-compose", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    evidence = capture(physical=a.physical_disposable_compose)
    require(verify(evidence), "invalid synthetic Compose evidence schema")
    print(json.dumps(evidence, sort_keys=True, indent=2 if a.json else None))
    if a.physical_disposable_compose and not evidence["physical_canary_executed"]:
        raise SystemExit(2)
