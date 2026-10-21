#!/usr/bin/env python3
"""Disposable Hermes rootless NORMAL-ENTRYPOINT startup and exact overlay pair.

Opt-in ONLY on x1-370. Builds two isolated, no-network/no-pull candidate
images from the EXACT local image backing the running Hermes container.
Replaces only /entrypoint.sh with the pinned research rootless candidate.
Runs one disposable container with real strict_executor_main, but all task
polling is intercepted by a LOCAL SYNTHETIC sitecustomize fixture. Does
NOT start the Hermes CLI, access any provider, or touch production mounts,
keys, data, containers, network, Tailscale or Compose.

A PASS proves narrowly repeatable local overlay + normal synthetic startup,
NOT an independently attested image or a real task/production release.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import assistx_hermes_container_canary as shell

SOURCE_DATE_EPOCH = "1704067200"
NORMAL_MARKER = "ROOTLESS_HERMES_NORMAL_MAIN_PASS"
BASE_IMAGE_ID = shell.IMAGE_ID_RE
TAG_RE = re.compile(r"^og-hermes-normal-(?:base|overlay-a|overlay-b)-[0-9a-f]{16}$")
CONTAINER_RE = re.compile(r"^og-hermes-normal-test-[0-9a-f]{16}$")
STABLE_BASE_TAG = "og-hermes-normal-base-" + "0"*16
STABLE_BASE_LOCK = shell.SCRATCH_PARENT / ".og-hermes-normal-build.lock"
ROOT_RE = re.compile(r"^og-hermes-normal-fixture-[a-z0-9_]{8}$")
ENTRY = Path(__file__).with_name("hermes_rootless_entrypoint_candidate.sh")
SITE = Path(__file__).with_name("hermes_synthetic_sitecustomize.py")
DOCKERFILE = """ARG BASE
FROM ${BASE}
COPY --chown=0:0 entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh
"""
MOCK_ENV = (
    "HOME=/workspace",
    "HERMES_HOME=/workspace/hermes-home",
    "HERMES_CONFIG_HOME=/workspace/.hermes",
    "PYTHONPATH=/workspace:/app/src",
    "ASSISTX_URL=http://127.0.0.1:1",
    "ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN=SYNTHETIC_BOOTSTRAP_ONLY",
    "ASSISTX_STRICT_EXECUTOR_AUTH=true",
    "HERMES_AGENT_ID=hermes-synthetic-canary",
    "HERMES_SELFTASK_ENABLED=false",
    "HERMES_SELFTASK_BULK_MODELS=",
    "HERMES_EVAL_PATH=/workspace/eval.json",
    "HERMES_KNOWLEDGE_ROOT=/workspace/knowledge",
    "PYTHONDONTWRITEBYTECODE=1",
)
TRUE_CHECKS = (
    "live_hermes_image_id_pinned_before_and_after",
    "independent_timestamp_controlled_build_ids_equal",
    "candidate_entrypoint_used_without_override",
    "readonly_networkless_nonroot_container_before_start",
    "exact_two_disposable_synthetic_mounts",
    "normal_strict_executor_one_mock_poll",
    "no_credentials_persisted",
    "synthetic_knowledge_write_denied",
    "source_and_production_containers_untouched",
    "disposable_images_container_and_fixture_removed",
)
FALSE_AUTHORITY = (
    "production_deployment_authorized",
    "agent_dispatch_authorized",
    "model_egress_authorized",
    "production_containers_modified",
    "real_source_owner_authority_verified",
    "true_production_evidence_write_denial_verified",
    "independently_signed_source_build_attested",
    "production_hermes_workflow_qualified",
)
BLOCKERS = (
    "production_hermes_mount_risk_still_unresolved",
    "real_task_claim_heartbeat_completion_unverified",
    "production_source_and_image_provenance_unattested",
    "real_model_owner_auth_revocation_unverified",
    "separate_reviewer_and_rollout_approval_missing",
)


class UnsafeNormalStartup(ValueError):
    pass


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise UnsafeNormalStartup("missing or redirected research fixture")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _call(command: list[str], *, seconds: int = 16, env=None):
    return subprocess.run(
        command, capture_output=True, text=True,
        timeout=seconds, check=False, env=env,
    )


def _safe_tag(kind: str, suffix: str) -> str:
    name=f"og-hermes-normal-{kind}-{suffix}"
    if not TAG_RE.fullmatch(name):
        raise UnsafeNormalStartup("invalid disposable image reference")
    return name


def _dockerfile_context(root: Path) -> Path:
    src=ENTRY
    candidate=SITE
    sha256_file(src)
    sha256_file(candidate)
    ctx=root/"docker-build"
    ctx.mkdir(mode=0o755)
    target=ctx/"entrypoint.sh"
    shutil.copyfile(src,target)
    target.chmod(0o644)
    (ctx/"Dockerfile").write_text(DOCKERFILE,encoding="utf8")
    (ctx/"Dockerfile").chmod(0o644)
    # Docker COPY may preserve tar context file mtimes in a layer.
    # Normalize all authored build inputs, not only the image config
    # Created timestamp. Never modify the checked-in source file.
    epoch=int(SOURCE_DATE_EPOCH)
    for authored in (target,ctx/"Dockerfile"):
        os.utime(authored,(epoch,epoch))
    if sha256_file(target)!=sha256_file(src):
        raise UnsafeNormalStartup("candidate entrypoint mismatch after copy")
    return ctx


def _build_argv(ctx: Path, base_tag: str, overlay_tag: str) -> list[str]:
    if not TAG_RE.fullmatch(base_tag) or not TAG_RE.fullmatch(overlay_tag):
        raise UnsafeNormalStartup("invalid bounded fixture image reference")
    if not ctx.is_dir() or ctx.name!="docker-build":
        raise UnsafeNormalStartup("wrong disposable build context")
    return [
        "docker","build","--network=none","--pull=false",
        "--no-cache","--provenance=false",
        "--build-arg",f"BASE={base_tag}",
        "--build-arg",f"SOURCE_DATE_EPOCH={SOURCE_DATE_EPOCH}",
        "--tag",overlay_tag,"--file",str(ctx/"Dockerfile"),str(ctx),
    ]


def _container_argv(image_id: str, name: str,
                    knowledge: Path, workspace: Path) -> list[str]:
    if not BASE_IMAGE_ID.fullmatch(image_id) or not CONTAINER_RE.fullmatch(name):
        raise UnsafeNormalStartup("invalid pinned image or canary container")
    root=knowledge.parent
    if (root != workspace.parent or root.parent != shell.SCRATCH_PARENT
            or not ROOT_RE.fullmatch(root.name)
            or not knowledge.is_dir() or not workspace.is_dir()
            or knowledge.is_symlink() or workspace.is_symlink()):
        raise UnsafeNormalStartup("scratch must be an exact private test root")
    return [
        "docker","create","--pull=never","--name",name,
        "--network=none","--read-only","--user=1000:1000",
        "--cap-drop=ALL","--security-opt=no-new-privileges:true",
        "--pids-limit=32","--memory=384m","--cpus=0.5",
        "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16777216",
        "--mount",f"type=bind,src={knowledge},dst=/research/knowledge,readonly",
        "--mount",f"type=bind,src={workspace},dst=/workspace",
        "--workdir=/workspace",
        *[f"--env={x}" for x in MOCK_ENV],
        image_id, "python3","-B","-m","assistx.strict_executor_main",
    ]


def _inspect(name: str, image: str, knowledge: Path, workspace: Path) -> None:
    # Only inspect non-secret portions of a single purpose-made container.
    r=_call(["docker","inspect","--format",
             "{{json .HostConfig}}",name],seconds=7)
    mounts=_call(["docker","inspect","--format",
                  "{{json .Mounts}}",name],seconds=7)
    user=_call(["docker","inspect","--format",
               "{{.Config.User}}",name],seconds=7)
    entry=_call(["docker","inspect","--format",
                "{{json .Config.Entrypoint}}",name],seconds=7)
    cmd=_call(["docker","inspect","--format",
              "{{json .Config.Cmd}}",name],seconds=7)
    actual=_call(["docker","inspect","--format",
                 "{{.Image}}",name],seconds=7)
    if (any(x.returncode!=0 or len(x.stdout)>50000 for x in
            (r,mounts,user,entry,cmd,actual))
            or actual.stdout.strip()!=image):
        raise UnsafeNormalStartup("disposable container static inspect failed")
    try:
        hc=json.loads(r.stdout)
        ms=json.loads(mounts.stdout)
        en=json.loads(entry.stdout)
        argv=json.loads(cmd.stdout)
    except (ValueError, TypeError) as e:
        raise UnsafeNormalStartup("invalid Docker static contract") from e
    actual_paths={m.get("Source") for m in ms}
    by_target={m.get("Destination"):m for m in ms}
    if (user.stdout.strip()!="1000:1000"
            or en!=["/entrypoint.sh"]
            or argv!=["python3","-B","-m","assistx.strict_executor_main"]
            or hc.get("NetworkMode")!="none"
            or hc.get("ReadonlyRootfs") is not True
            or hc.get("Privileged") is not False
            or hc.get("PidsLimit")!=32
            or hc.get("Memory")!=402653184
            or hc.get("NanoCpus")!=500000000
            or "ALL" not in (hc.get("CapDrop") or [])
            or not any("no-new-privileges" in x
                       for x in (hc.get("SecurityOpt") or []))
            or not isinstance(ms,list) or len(ms)!=2
            or actual_paths!={str(knowledge),str(workspace)}
            or set(by_target)!={"/research/knowledge","/workspace"}
            or by_target["/research/knowledge"].get("RW") is not False
            or by_target["/workspace"].get("RW") is not True):
        raise UnsafeNormalStartup("disposable Hermes startup not confined")


def _absent_docker_object(category: str, name: str) -> bool:
    cmd=(["docker","image","inspect",name] if category=="image" else
         ["docker","container","inspect",name])
    result=_call(cmd,seconds=6)
    return result.returncode!=0


@contextmanager
def _exclusive_fixed_base_tag():
    """Atomic local-only serialization for a stable offline base reference.

    The first two-build comparison used a random base name and passed,
    but two *different* invocations produced different image IDs.
    Never silently treat a randomized FROM build arg as global
    reproducibility evidence. A preexisting tag/lock blocks the canary;
    we NEVER retag or delete somebody else's image.
    """
    if not TAG_RE.fullmatch(STABLE_BASE_TAG):
        raise UnsafeNormalStartup("invalid exclusive base name")
    STABLE_BASE_LOCK.mkdir(mode=0o700,parents=False,exist_ok=False)
    try:
        if not _absent_docker_object("image", STABLE_BASE_TAG):
            raise UnsafeNormalStartup("test base tag already exists, refusing overwrite")
        yield
    finally:
        STABLE_BASE_LOCK.rmdir()


def _run_two_offline_builds_and_main(live_id: str) -> tuple[dict,str|None]:
    with _exclusive_fixed_base_tag():
        return _run_under_fixed_base(live_id)


def _run_under_fixed_base(live_id: str) -> tuple[dict,str|None]:
    suffix=uuid.uuid4().hex[:16]
    base=STABLE_BASE_TAG
    overlay1=_safe_tag("overlay-a",suffix)
    overlay2=_safe_tag("overlay-b",suffix)
    container=f"og-hermes-normal-test-{suffix}"
    if not CONTAINER_RE.fullmatch(container):
        raise UnsafeNormalStartup("invalid scratch container name")
    checks={k:False for k in TRUE_CHECKS}
    created_tags=[]
    created_container=False
    with tempfile.TemporaryDirectory(
            prefix="og-hermes-normal-fixture-",
            dir=shell.SCRATCH_PARENT) as tmp:
        root=Path(tmp)
        if root.parent != shell.SCRATCH_PARENT or not ROOT_RE.fullmatch(root.name):
            raise UnsafeNormalStartup("disposable root not on approved host")
        # All test files are synthetic. The root is traversable by the
        # nonroot UID inside Docker, but is deleted by TemporaryDirectory.
        root.chmod(0o755)
        knowledge=root/"knowledge"
        worktree=root/"workspace"
        knowledge.mkdir(mode=0o755)
        worktree.mkdir(mode=0o755)
        (knowledge/"synthetic-source.txt").write_text(
            "SYNTHETIC_ONLY\n",encoding="utf8")
        (knowledge/"synthetic-source.txt").chmod(0o644)
        site=worktree/"sitecustomize.py"
        shutil.copyfile(SITE,site)
        site.chmod(0o644)
        if sha256_file(site)!=sha256_file(SITE):
            raise UnsafeNormalStartup("test-only intercept fixture changed")
        ctx=_dockerfile_context(root)
        ids=[]
        try:
            # Record fixed base tag before attempting creation so even
            # a partially completed tag operation is removed by cleanup.
            created_tags.append(base)
            tag=_call(["docker","tag",live_id,base],seconds=8)
            if tag.returncode:
                raise UnsafeNormalStartup("cannot pin running Hermes base image")
            env={**os.environ,"SOURCE_DATE_EPOCH":SOURCE_DATE_EPOCH,
                 "DOCKER_BUILDKIT":"1"}
            for candidate in (overlay1,overlay2):
                # Record the only possible disposable tag before the
                # build, so a partial successful build is cleaned too.
                created_tags.append(candidate)
                build=_call(_build_argv(ctx,base,candidate),
                            seconds=90,env=env)
                if build.returncode:
                    raise UnsafeNormalStartup("offline rootless overlay build rejected")
                result=_call(["docker","image","inspect","--format",
                              "{{.Id}}",candidate],seconds=6)
                if result.returncode or not BASE_IMAGE_ID.fullmatch(result.stdout.strip()):
                    raise UnsafeNormalStartup("candidate image object invalid")
                ids.append(result.stdout.strip())
            checks["independent_timestamp_controlled_build_ids_equal"]= (
                len(ids)==2 and ids[0]==ids[1])
            if not checks["independent_timestamp_controlled_build_ids_equal"]:
                raise UnsafeNormalStartup("two no-cache overlay builds differ")
            created=_call(
                _container_argv(ids[0],container,knowledge,worktree),seconds=18)
            if created.returncode:
                raise UnsafeNormalStartup("unable to create disposable Hermes image")
            created_container=True
            _inspect(container,ids[0],knowledge,worktree)
            checks["candidate_entrypoint_used_without_override"]=True
            checks["readonly_networkless_nonroot_container_before_start"]=True
            checks["exact_two_disposable_synthetic_mounts"]=True
            started=_call(["docker","start","--attach",container],seconds=40)
            exitcode=_call(["docker","inspect","--format",
                            "{{.State.ExitCode}}",container],seconds=7)
            marker=worktree/"verified.marker"
            if (started.returncode!=0 or
                    started.stdout.splitlines().count(NORMAL_MARKER)!=1 or
                    exitcode.returncode!=0 or exitcode.stdout.strip()!="0" or
                    not marker.is_file() or
                    marker.read_text().strip()!=NORMAL_MARKER):
                raise UnsafeNormalStartup("Hermes strict executor synthetic startup failed")
            checks["normal_strict_executor_one_mock_poll"]=True
            # Interceptor asserts both rootfs and synthetic knowledge write
            # denial and one poll, before writing this exact marker.
            checks["synthetic_knowledge_write_denied"]=True
            checks["no_credentials_persisted"]= (
                not list(worktree.rglob(".env")) and
                (worktree/"hermes-home/config.yaml").is_file() and
                (worktree/".hermes/config.yaml").is_symlink())
            if not checks["no_credentials_persisted"]:
                raise UnsafeNormalStartup("unexpected credential-seed or config")
            checks["live_hermes_image_id_pinned_before_and_after"]= (
                shell._image_id()==live_id)
            checks["source_and_production_containers_untouched"]= (
                checks["live_hermes_image_id_pinned_before_and_after"]
                and _call(["docker","inspect","--format",
                           "{{.State.Running}}",shell.IMAGE_NAME],
                          seconds=7).stdout.strip()=="true")
        finally:
            if created_container:
                _call(["docker","rm","--force",container],seconds=12)
            for tag in reversed(created_tags):
                _call(["docker","image","rm",tag],seconds=12)
            all_absent=(
                _absent_docker_object("container",container)
                and all(_absent_docker_object("image",tag)
                        for tag in (base,overlay1,overlay2)))
            checks["disposable_images_container_and_fixture_removed"]=all_absent
            # TemporaryDirectory cleans only disposable synthetic fixture.
    return checks, (ids[0] if len(ids)==2 and ids[0]==ids[1] else None)


def capture(*, physical: bool) -> dict:
    report={
        "schema":1,
        "mode":"assistx_hermes_two_build_rootless_normal_startup",
        "host":"x1-370",
        "physical_canary_executed":False,
        "exact_running_base_image":None,
        "disposable_reproducible_overlay_image":None,
        **{k:False for k in TRUE_CHECKS},
        **{k:False for k in FALSE_AUTHORITY},
        "provider_requests_forwarded":0,
        "real_agent_tasks_executed":0,
        "decision":"HOLD",
        "blockers":list(BLOCKERS),
    }
    if not physical:
        return report
    try:
        if os.uname().nodename!="x1-370" or os.geteuid()==0:
            raise UnsafeNormalStartup("canary restricted to x1 ordinary operator")
        shell.SCRATCH_PARENT.mkdir(mode=0o700,parents=True,exist_ok=True)
        live=shell._image_id()
        if not BASE_IMAGE_ID.fullmatch(live):
            raise UnsafeNormalStartup("invalid production source image")
        report["exact_running_base_image"]=live
        results, overlay=_run_two_offline_builds_and_main(live)
        report.update(results)
        report["disposable_reproducible_overlay_image"]=overlay
        report["physical_canary_executed"]=all(results.values())
        if not report["physical_canary_executed"]:
            report["blockers"].append("disposable_normal_startup_checks_incomplete")
    except (UnsafeNormalStartup,OSError,ValueError,TypeError,
            subprocess.TimeoutExpired):
        report["blockers"].append("disposable_normal_startup_failed_closed")
    return report


def validate(report: object) -> bool:
    expected={
        "schema","mode","host","physical_canary_executed",
        "exact_running_base_image","disposable_reproducible_overlay_image",
        *TRUE_CHECKS,*FALSE_AUTHORITY,
        "provider_requests_forwarded","real_agent_tasks_executed",
        "decision","blockers",
    }
    if not isinstance(report,dict) or set(report)!=expected:
        return False
    if (type(report["schema"]) is not int or report["schema"]!=1
            or report["mode"]!="assistx_hermes_two_build_rootless_normal_startup"
            or report["host"]!="x1-370"
            or type(report["physical_canary_executed"]) is not bool
            or (report["exact_running_base_image"] is not None
                and (not isinstance(report["exact_running_base_image"],str) or
                    not BASE_IMAGE_ID.fullmatch(report["exact_running_base_image"])))
            or (report["disposable_reproducible_overlay_image"] is not None
                and (not isinstance(report["disposable_reproducible_overlay_image"],str)
                     or not BASE_IMAGE_ID.fullmatch(report["disposable_reproducible_overlay_image"])))
            or any(type(report[k]) is not bool for k in TRUE_CHECKS)
            or any(report[k] is not False for k in FALSE_AUTHORITY)
            or type(report["provider_requests_forwarded"]) is not int
            or report["provider_requests_forwarded"]!=0
            or type(report["real_agent_tasks_executed"]) is not int
            or report["real_agent_tasks_executed"]!=0
            or report["decision"]!="HOLD"
            or not isinstance(report["blockers"],list)
            or not all(isinstance(k,str) and 1<=len(k)<=110 for k in report["blockers"])
            or not all(x in report["blockers"] for x in BLOCKERS)):
        return False
    if report["physical_canary_executed"] != all(report[k] for k in TRUE_CHECKS):
        return False
    if report["physical_canary_executed"] and (
            report["exact_running_base_image"] is None
            or report["disposable_reproducible_overlay_image"] is None):
        return False
    return True


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--physical-disposable-normal-startup",action="store_true")
    p.add_argument("--json",action="store_true")
    args=p.parse_args()
    record=capture(physical=args.physical_disposable_normal_startup)
    if not validate(record):
        raise SystemExit("invalid/reconfigured Hermes rootless evidence")
    print(json.dumps(record,indent=2 if args.json else None,sort_keys=True))
    if args.physical_disposable_normal_startup and not record["physical_canary_executed"]:
        raise SystemExit(2)
