#!/usr/bin/env python3
"""Disposable local Hermes IMAGE containment canary; NEVER run its agent.

One isolated container is created from the EXACT image ID currently
deployed for AssistX Hermes on x1, with the default ENTRYPOINT overridden
by /bin/sh. No production volumes, secrets, network, command execution
against real host data, or provider calls. Two synthetic scratch dirs
simulate (1) read-only knowledge and (2) narrowly writable workspace.
No running Hermes or Docker Compose service is changed.

This does not prove the real Hermes workflow works in the replacement.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path

IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTAINER_NAME_RE = re.compile(r"^og-hermes-image-canary-[0-9a-f]{20}$")
SCRATCH_RE = re.compile(r"^og-hermes-containment-[A-Za-z0-9_]{8}$")
IMAGE_NAME = "assistx-hermes-adapter"
SCRATCH_PARENT = Path("/home/scott/.cache")
DOCKER = "docker"
# Zero access to real knowledge, the SSD, NAS, git repositories, SSH keys,
# API credential files, Docker socket or AssistX networks is authorized.
SHELL_SCRIPT = r"""set -eu
test "$(id -u)" != "0"
test -r /research/knowledge/synthetic-source.txt
test "$(cat /research/knowledge/synthetic-source.txt)" = "SYNTHETIC_ONLY"
test ! -e /media/scott/SSD_4TB
test ! -e /media/scott/NAS5
test ! -e /root/knowledge
test ! -e /root/.ssh/id_hermes
test ! -e /root/.ssh/id_rsa
test ! -e /root/.ssh/id_ed25519
if (printf blocked > /research/knowledge/attempted-write) 2>/dev/null; then
  exit 32
fi
if (printf blocked > /etc/attempted-write) 2>/dev/null; then
  exit 33
fi
test "$(sed -n 's/^NoNewPrivs:[[:space:]]*//p' /proc/self/status)" = "1"
test "$(sed -n 's/^CapEff:[[:space:]]*//p' /proc/self/status)" = "0000000000000000"
printf 'WORKSPACE_CANARY_ONLY\n' > /workspace/synthetic-output.txt
test "$(cat /workspace/synthetic-output.txt)" = "WORKSPACE_CANARY_ONLY"
printf '%s\n' "ISOLATED_HERMES_IMAGE_SHELL_PASS"
"""
REQUIRED_CHECKS = (
    "actual_running_hermes_image_id_pinned",
    "default_agent_entrypoint_overridden",
    "no_network_and_no_production_mounts",
    "container_nonroot_readonly_and_capdrop",
    "read_only_synthetic_knowledge_write_denied",
    "container_rootfs_write_denied",
    "synthetic_workspace_writable",
    "no_prod_source_or_provider_call",
    "one_shot_container_removed",
)
ABSENT_AUTHORITIES = (
    "agent_dispatch_authorized",
    "model_egress_authorized",
    "production_deployment_authorized",
    "authenticated_model_catalog_verified",
    "physical_production_evidence_write_denial_verified",
    "hermes_workflow_compatibility_verified",
    "independent_custody_verified",
    "true_source_effect_fencing_verified",
)


class UnsafeCanary(RuntimeError):
    pass


def _exec(command: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True,
                          timeout=timeout, check=False)


def _image_id() -> str:
    r = _exec([DOCKER, "inspect", "--format", "{{.Image}}",
               IMAGE_NAME], timeout=8)
    candidate = r.stdout.strip()
    if r.returncode or not IMAGE_ID_RE.fullmatch(candidate):
        raise UnsafeCanary("running Hermes exact local image unavailable")
    return candidate


def docker_create_argv(image_id: str, container_name: str,
                       knowledge: Path, worktree: Path) -> list[str]:
    """No caller-controlled Docker flags, executable or network endpoints."""
    if not IMAGE_ID_RE.fullmatch(image_id):
        raise UnsafeCanary("untrusted Docker image ID")
    if not CONTAINER_NAME_RE.fullmatch(container_name):
        raise UnsafeCanary("untrusted scratch container name")
    for path in (knowledge,worktree):
        if (not path.is_absolute() or path.is_symlink() or
                not path.is_dir() or path.stat().st_uid != os.getuid()):
            raise UnsafeCanary("disposable synthetic scratch missing or unsafe")
    if knowledge.parent != worktree.parent:
        raise UnsafeCanary("synthetic bind roots differ")
    root=knowledge.parent
    if (root.parent != SCRATCH_PARENT or
            not SCRATCH_RE.fullmatch(root.name)):
        raise UnsafeCanary("scratch must be an exact private fixture root")
    if os.getuid() == 0 or os.getgid() == 0:
        raise UnsafeCanary("disposable runner must be nonroot on host")
    return [
        DOCKER, "create", "--name", container_name,
        "--pull=never",
        "--network=none",
        "--read-only",
        f"--user={os.getuid()}:{os.getgid()}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--pids-limit=32",
        "--memory=256m",
        "--cpus=0.5",
        "--workdir=/workspace",
        "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m",
        "--mount", f"type=bind,src={knowledge},dst=/research/knowledge,readonly",
        "--mount", f"type=bind,src={worktree},dst=/workspace",
        "--entrypoint=/bin/sh",
        image_id, "-c", SHELL_SCRIPT,
    ]


def _inspect_created(name: str, image_id: str, expected_sources: set[str]) -> bool:
    # Explicitly no Config.Env or source code/file reads; filtered
    # attributes only. Non-execution fields are checked before start.
    requests = {
        "user": "{{.Config.User}}",
        "entrypoint": "{{json .Config.Entrypoint}}",
        "image": "{{.Image}}",
        "host": "{{json .HostConfig}}",
        "mounts": "{{json .Mounts}}",
    }
    output={}
    for k,fmt in requests.items():
        r=_exec([DOCKER,"inspect","--format",fmt,name],timeout=8)
        if r.returncode or not 1 <= len(r.stdout) <= 50_000:
            raise UnsafeCanary("cannot inspect scratch container confinement")
        output[k]=r.stdout.strip()
    hc=json.loads(output["host"])
    mounts=json.loads(output["mounts"])
    entry=json.loads(output["entrypoint"])
    present_sources={m.get("Source") for m in mounts}
    if (output["image"]!=image_id
            or output["user"]!=f"{os.getuid()}:{os.getgid()}"
            or entry != ["/bin/sh"]
            or hc.get("NetworkMode")!="none"
            or hc.get("ReadonlyRootfs") is not True
            or "ALL" not in (hc.get("CapDrop") or [])
            or not any("no-new-privileges" in x
                       for x in (hc.get("SecurityOpt") or []))
            or hc.get("Privileged") is not False
            or present_sources != expected_sources
            or len(mounts)!=2):
        raise UnsafeCanary("scratch container failed static confinement check")
    requested={m.get("Destination"):m for m in mounts}
    if (set(requested)!={"/research/knowledge","/workspace"}
            or requested["/research/knowledge"].get("RW") is not False
            or requested["/workspace"].get("RW") is not True):
        raise UnsafeCanary("unexpected scratch mount permission")
    return True


def _run_one(image_id: str) -> dict[str,bool]:
    name="og-hermes-image-canary-"+uuid.uuid4().hex[:20]
    if not CONTAINER_NAME_RE.fullmatch(name):
        raise UnsafeCanary("internal scratch name invalid")
    checks={k:False for k in REQUIRED_CHECKS}
    with tempfile.TemporaryDirectory(
            prefix="og-hermes-containment-", dir=SCRATCH_PARENT) as rootstr:
        root=Path(rootstr)
        if root.parent!=SCRATCH_PARENT or not SCRATCH_RE.fullmatch(root.name):
            raise UnsafeCanary("untrusted system temporary root")
        # These paths contain only synthetic bytes. 0755 allows Docker's
        # non-root UID to read; directory remains owned by original user.
        root.chmod(0o755)
        knowledge=root/"knowledge"
        worktree=root/"worktree"
        for d in (knowledge,worktree):
            d.mkdir(mode=0o755)
        (knowledge/"synthetic-source.txt").write_text("SYNTHETIC_ONLY\n")
        (knowledge/"synthetic-source.txt").chmod(0o644)
        try:
            argv=docker_create_argv(image_id,name,knowledge,worktree)
            out=_exec(argv,timeout=20)
            if out.returncode or len(out.stdout)>512:
                raise UnsafeCanary("no safe scratch container created")
            checks["actual_running_hermes_image_id_pinned"]=True
            checks["default_agent_entrypoint_overridden"]=True
            if not _inspect_created(name,image_id,{str(knowledge),str(worktree)}):
                raise UnsafeCanary("scratch runtime confinement could not be checked")
            checks["no_network_and_no_production_mounts"]=True
            checks["container_nonroot_readonly_and_capdrop"]=True
            run=_exec([DOCKER,"start","--attach",name],timeout=25)
            if run.returncode or run.stdout.strip()!="ISOLATED_HERMES_IMAGE_SHELL_PASS":
                raise UnsafeCanary("synthetic image shell containment failed")
            state=_exec([DOCKER,"inspect","--format",
                         "{{.State.ExitCode}}",name],timeout=8)
            if state.returncode or state.stdout.strip()!="0":
                raise UnsafeCanary("scratch process did not exit successfully")
            checks["read_only_synthetic_knowledge_write_denied"]=True
            checks["container_rootfs_write_denied"]=True
            checks["synthetic_workspace_writable"]= (
                (worktree/"synthetic-output.txt").read_text()=="WORKSPACE_CANARY_ONLY\n")
            checks["no_prod_source_or_provider_call"]=True
        finally:
            # Only ever remove a randomized, dedicated test container;
            # even a failed Docker create may have left a partial
            # container. Never run Compose down or touch prod containers.
            try:
                _exec([DOCKER,"rm","--force",name],timeout=12)
                chk=_exec([DOCKER,"inspect","--format",
                           "{{.Id}}",name],timeout=6)
                checks["one_shot_container_removed"]=chk.returncode!=0
            except (OSError,subprocess.TimeoutExpired):
                checks["one_shot_container_removed"]=False
    return checks


def capture(*, enable_physical: bool) -> dict:
    record={
        "schema":1,"mode":"assistx_hermes_disposable_image_containment",
        "host":"x1-370", "target":"synthetic_hermes_image_shell",
        "physical_canary_executed":False,
        "exact_running_image_object_sha256":None,
        **{x:False for x in REQUIRED_CHECKS},
        **{x:False for x in ABSENT_AUTHORITIES},
        "provider_requests_forwarded":0,
        "production_containers_modified":False,
        "decision":"HOLD",
        "blockers":[
            "production_hermes_mount_risk_unresolved",
            "production_write_denial_not_qualified",
            "hermes_agent_entrypoint_and_workflow_not_executed",
            "source_image_git_provenance_unverified",
            "source_owner_attestation_revocation_unverified",
        ],
    }
    if not enable_physical:
        return record
    try:
        if os.uname().nodename!="x1-370":
            raise UnsafeCanary("physical canary only approved on x1-370")
        SCRATCH_PARENT.mkdir(mode=0o700,parents=True,exist_ok=True)
        exact=_image_id()
        record["exact_running_image_object_sha256"]=exact
        checks=_run_one(exact)
        if _image_id() != exact:
            raise UnsafeCanary("running Hermes image changed mid-canary")
        record.update(checks)
        record["physical_canary_executed"]=True
    except (UnsafeCanary,OSError,ValueError,TypeError,
            subprocess.TimeoutExpired,PermissionError):
        record["blockers"].append("synthetic_container_canary_failed_closed")
    return record


def verify(rec: object) -> bool:
    fields={
        "schema","mode","host","target","physical_canary_executed",
        "exact_running_image_object_sha256",*REQUIRED_CHECKS,
        *ABSENT_AUTHORITIES,"provider_requests_forwarded",
        "production_containers_modified","decision","blockers",
    }
    if not isinstance(rec,dict) or set(rec)!=fields:
        return False
    if (type(rec["schema"]) is not int or rec["schema"]!=1
            or rec["mode"]!="assistx_hermes_disposable_image_containment"
            or rec["host"]!="x1-370"
            or rec["target"]!="synthetic_hermes_image_shell"
            or type(rec["physical_canary_executed"]) is not bool
            or (rec["exact_running_image_object_sha256"] is not None
                and (not isinstance(rec["exact_running_image_object_sha256"],str)
                or not IMAGE_ID_RE.fullmatch(rec["exact_running_image_object_sha256"])))
            or any(type(rec[k]) is not bool for k in REQUIRED_CHECKS)
            or any(rec[k] is not False for k in ABSENT_AUTHORITIES)
            or rec["production_containers_modified"] is not False
            or type(rec["provider_requests_forwarded"]) is not int
            or rec["provider_requests_forwarded"]!=0
            or rec["decision"]!="HOLD"
            or not isinstance(rec["blockers"],list)
            or not all(isinstance(x,str) and 1<=len(x)<=100 for x in rec["blockers"])
            or not all(k in rec["blockers"] for k in (
                "production_hermes_mount_risk_unresolved",
                "production_write_denial_not_qualified",
                "hermes_agent_entrypoint_and_workflow_not_executed",
                "source_image_git_provenance_unverified",
                "source_owner_attestation_revocation_unverified"))):
        return False
    if rec["physical_canary_executed"]:
        if (not all(rec[k] for k in REQUIRED_CHECKS)
                or rec["exact_running_image_object_sha256"] is None):
            return False
    elif any(rec[k] for k in REQUIRED_CHECKS):
        # Partial results from failed physical trials are untrusted.
        return False
    return True


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-disposable-canary",action="store_true",
                        help="Create/start/remove ONLY one sandboxed synthetic"
                             " Hermes image shell on x1, no agent entrypoint")
    parser.add_argument("--json",action="store_true")
    args=parser.parse_args()
    result=capture(enable_physical=args.physical_disposable_canary)
    if not verify(result):
        raise SystemExit("untrusted Hermes containment canary")
    print(json.dumps(result,indent=2 if args.json else None,sort_keys=True))
    if args.physical_disposable_canary and (
            not all(result[k] for k in REQUIRED_CHECKS)):
        raise SystemExit(2)
