#!/usr/bin/env python3
"""Opt-in cross-container private Docker network Hermes fake AssistX canary.

Both containers run exact pinned local image, with only synthetic scratch.
Never connect either container to production networks, publish ports, copy
secrets, run providers, modify Compose, or grant production dispatch.
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

WORKER = Path(__file__).with_name("hermes_private_net_worker_sitecustomize.py")
SERVER = Path(__file__).with_name("hermes_private_net_api_server.py")
NETWORK = re.compile(r"^og-hermes-net-[0-9a-f]{16}$")
SERVER_NAME = re.compile(r"^og-hermes-net-server-[0-9a-f]{16}$")
REQUIRED = (
    "production_image_pinned",
    "reproducible_offline_rootless_overlay",
    "private_internal_bridge_verified",
    "server_and_worker_separate_containers",
    "normal_rootless_worker_verified",
    "exact_synthetic_two_mounts_each",
    "http_task_claim_heartbeat_done",
    "real_container_stop_denies_stale_completion",
    "server_restart_preserves_active_lease",
    "synthetic_expiry_fences_old_claim_and_token",
    "synthetic_completion_replay_denied",
    "zero_real_agent_or_model_requests",
    "all_disposable_docker_objects_removed",
    "production_hermes_running_unchanged",
)
BLOCKERS = (
    "production_hermes_writable_ssd_mounts_unresolved",
    "independent_image_provenance_unverified",
    "real_owner_authorized_task_execution_unverified",
    "independent_lease_fencing_key_custody_not_established",
    "physical_host_separation_and_reviewer_signoff_missing",
)

class UnsafePrivateNet(RuntimeError):
    pass

def need(ok, reason):
    if not ok:
        raise UnsafePrivateNet(reason)

def call(argv, timeout=15):
    return normal._call(argv, seconds=timeout)

def inspect_json(fmt, name):
    r = call(["docker", "inspect", "--format", fmt, name], 7)
    need(r.returncode == 0 and 0 < len(r.stdout) < 50000,
         "cannot inspect disposable Docker object")
    return json.loads(r.stdout)

def network_ok(name, *, expected_containers=None):
    need(NETWORK.fullmatch(name), "invalid synthetic network")
    r = call(["docker", "network", "inspect", "--format", "{{json .}}", name], 7)
    need(r.returncode == 0 and len(r.stdout) < 65000,
         "cannot inspect synthetic network")
    doc = json.loads(r.stdout)
    need(doc.get("Name") == name and doc.get("Internal") is True
         and doc.get("Driver") == "bridge" and doc.get("Scope") == "local"
         and doc.get("Attachable") is False,
         "synthetic network not internal/private")
    if expected_containers is not None:
        actual = set((doc.get("Containers") or {}).keys())
        need(actual.issubset(expected_containers),
             "disposable network gained unexpected endpoints")
    return True

def contained(name, image, network, knowledge, scratch, *, worker):
    hc = inspect_json("{{json .HostConfig}}", name)
    mounts = inspect_json("{{json .Mounts}}", name)
    config = inspect_json("{{json .Config}}", name)
    actual_image = call(["docker", "inspect", "--format", "{{.Image}}", name], 7)
    need(actual_image.returncode == 0 and actual_image.stdout.strip() == image,
         "wrong image for disposable container")
    need(config.get("User") == "1000:1000"
         and hc.get("NetworkMode") == network
         and hc.get("ReadonlyRootfs") is True
         and hc.get("Privileged") is False
         and hc.get("PidsLimit") == 32
         and hc.get("Memory") in (268435456, 402653184)
         and hc.get("NanoCpus") == 500000000
         and "ALL" in (hc.get("CapDrop") or [])
         and any("no-new-privileges" in x for x in hc.get("SecurityOpt") or [])
         and not hc.get("PortBindings")
         and isinstance(mounts, list) and len(mounts) == 2,
         "unsafe disposable container execution limits")
    paths = {m.get("Destination"): m for m in mounts}
    need(set(paths) == {"/research/knowledge", "/workspace"}
         and paths["/research/knowledge"].get("Source") == str(knowledge)
         and paths["/research/knowledge"].get("RW") is False
         and paths["/workspace"].get("Source") == str(scratch)
         and paths["/workspace"].get("RW") is True,
         "unexpected bind mount/permission")
    need(config.get("Entrypoint") == (
             ["/entrypoint.sh"] if worker else ["python3"]),
         "unexpected disposable entrypoint")
    if worker:
        need(config.get("Cmd") == ["python3", "-B", "-m",
                                   "assistx.strict_executor_main"],
             "worker did not select strict executor main")
    else:
        need(config.get("Cmd") == [
            "-I", "-B", "/workspace/hermes_private_net_api_server.py",
            "18181", "/workspace/fake-assistx-fault-state.json"],
            "server executed a non-fixture program")
    return True

def worker_argv(image, name, network, server_name, knowledge, workspace):
    need(NETWORK.fullmatch(network) and SERVER_NAME.fullmatch(server_name),
         "invalid network/server")
    cmd = normal._container_argv(image, name, knowledge, workspace)
    need(cmd.count("--network=none") == 1, "missing isolation default")
    cmd[cmd.index("--network=none")] = "--network=" + network
    pos = cmd.index(image)
    cmd.insert(pos, "--env=FAKE_ASSISTX_URL=http://" + server_name + ":18181")
    return cmd

def server_argv(image, name, network, knowledge, workspace):
    need(shell.IMAGE_ID_RE.fullmatch(image) and SERVER_NAME.fullmatch(name)
         and NETWORK.fullmatch(network), "invalid server identity")
    need(knowledge.parent == workspace.parent
         and knowledge.parent.parent == shell.SCRATCH_PARENT
         and normal.ROOT_RE.fullmatch(knowledge.parent.name)
         and not knowledge.is_symlink() and not workspace.is_symlink(),
         "server scratch must be synthetic fixture")
    return ["docker", "create", "--pull=never", "--name", name,
            "--network=" + network, "--read-only", "--user=1000:1000",
            "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--pids-limit=32", "--memory=256m", "--cpus=0.5",
            "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16777216",
            "--mount", f"type=bind,src={knowledge},dst=/research/knowledge,readonly",
            "--mount", f"type=bind,src={workspace},dst=/workspace",
            "--workdir=/workspace", "--entrypoint=python3",
            image, "-I", "-B", "/workspace/hermes_private_net_api_server.py",
            "18181", "/workspace/fake-assistx-fault-state.json"]

def execute():
    need(os.uname().nodename == "x1-370" and os.geteuid() != 0,
         "cross-container pilot runs only as nonroot on x1-370")
    shell.SCRATCH_PARENT.mkdir(parents=True, mode=0o700, exist_ok=True)
    for fixture in (WORKER, SERVER):
        normal.sha256_file(fixture)
    live = shell._image_id()
    running = call(["docker", "inspect", "--format", "{{.State.Running}}",
                    shell.IMAGE_NAME], 7)
    need(running.returncode == 0 and running.stdout.strip() == "true",
         "live Hermes must remain running")
    with normal._exclusive_fixed_base_tag():
        return locked_run(live)

def locked_run(live):
    suffix = uuid.uuid4().hex[:16]
    network = "og-hermes-net-" + suffix
    server_name = "og-hermes-net-server-" + suffix
    worker_name = "og-hermes-normal-test-" + suffix
    base = normal.STABLE_BASE_TAG
    tags = [normal._safe_tag("overlay-a", suffix),
            normal._safe_tag("overlay-b", suffix)]
    checks = {key: False for key in REQUIRED}
    created_tags = []
    network_created = False
    created = set()
    image_id = None
    with tempfile.TemporaryDirectory(
        prefix="og-hermes-normal-fixture-", dir=shell.SCRATCH_PARENT
    ) as temp:
        root = Path(temp)
        need(root.parent == shell.SCRATCH_PARENT
             and normal.ROOT_RE.fullmatch(root.name), "bad temp root")
        root.chmod(0o755)
        knowledge = root / "knowledge"
        work = root / "worker"
        server = root / "server"
        for d in (knowledge, work, server):
            d.mkdir(mode=0o755)
        (knowledge / "synthetic-source.txt").write_text("SYNTHETIC_ONLY\n")
        (knowledge / "synthetic-source.txt").chmod(0o644)
        for src, dst in ((WORKER, work / "sitecustomize.py"),
                         (SERVER, server / "hermes_private_net_api_server.py")):
            shutil.copyfile(src, dst)
            dst.chmod(0o644)
            need(normal.sha256_file(src) == normal.sha256_file(dst),
                 "test-only program modified while copying")
        context = normal._dockerfile_context(root)
        need(normal._absent_docker_object("container", worker_name)
             and normal._absent_docker_object("container", server_name)
             and all(normal._absent_docker_object("image", name) for name in tags),
             "disposable identifier already occupied")
        try:
            create_net = call(["docker", "network", "create", "--driver", "bridge",
                               "--internal", "--label", "org.assistx.research=hermes",
                               network], 15)
            need(create_net.returncode == 0, "private network creation failed")
            network_created = True
            network_ok(network, expected_containers=set())
            checks["private_internal_bridge_verified"] = True
            created_tags.append(base)
            need(call(["docker", "tag", live, base], 8).returncode == 0,
                 "cannot pin local Hermes base image")
            ids = []
            env = {**os.environ, "DOCKER_BUILDKIT": "1",
                   "SOURCE_DATE_EPOCH": normal.SOURCE_DATE_EPOCH}
            for tag in tags:
                created_tags.append(tag)
                built = normal._call(normal._build_argv(context, base, tag),
                                     seconds=90, env=env)
                need(built.returncode == 0, "offline rootless build failed")
                result = call(["docker", "image", "inspect", "--format",
                               "{{.Id}}", tag], 6)
                need(result.returncode == 0 and
                     shell.IMAGE_ID_RE.fullmatch(result.stdout.strip()),
                     "no exact overlay image")
                ids.append(result.stdout.strip())
            need(ids[0] == ids[1], "independent offline builds differ")
            image_id = ids[0]
            checks["reproducible_offline_rootless_overlay"] = True
            checks["production_image_pinned"] = True
            for name, args in (
                (server_name, server_argv(image_id, server_name, network,
                                          knowledge, server)),
                (worker_name, worker_argv(image_id, worker_name, network,
                                          server_name, knowledge, work)),
            ):
                need(call(args, 20).returncode == 0,
                     "disposable container creation failed")
                created.add(name)
            contained(server_name, image_id, network, knowledge, server,
                      worker=False)
            contained(worker_name, image_id, network, knowledge, work,
                      worker=True)
            network_ids = set()
            for item in (server_name, worker_name):
                obj = call(["docker", "inspect", "--format", "{{.Id}}", item], 7)
                need(obj.returncode == 0 and len(obj.stdout.strip()) == 64,
                     "disposable container object not pinned")
                network_ids.add(obj.stdout.strip())
            network_ok(network, expected_containers=network_ids)
            checks["server_and_worker_separate_containers"] = True
            checks["normal_rootless_worker_verified"] = True
            checks["exact_synthetic_two_mounts_each"] = True
            checks["zero_real_agent_or_model_requests"] = True
            need(call(["docker", "start", server_name], 8).returncode == 0,
                 "fake isolated API could not start")
            server_state = call(["docker", "inspect", "--format", "{{.State.Running}}", server_name], 7)
            if server_state.stdout.strip() != "true":
                diagnostic = call(["docker", "logs", "--tail", "12", server_name], 7)
                raise UnsafePrivateNet("fake API exited before worker: " + diagnostic.stderr[-1300:])
            stages = (
                ("HERMES_PRIVATE_NET_STAGE_1_PASS", "http_task_claim_heartbeat_done"),
                ("HERMES_PRIVATE_NET_STAGE_2_PASS", "real_container_stop_denies_stale_completion"),
                ("HERMES_PRIVATE_NET_STAGE_3_PASS", "synthetic_expiry_fences_old_claim_and_token"),
            )
            for idx, (marker, check) in enumerate(stages):
                if idx == 1:
                    stopped = call(["docker", "stop", "--time=2", server_name], 10)
                    need(stopped.returncode == 0, "could not stop only fake API")
                    actual = call(["docker", "inspect", "--format",
                                   "{{.State.Running}}", server_name], 7)
                    need(actual.stdout.strip() == "false", "API did not stop")
                elif idx == 2:
                    need(call(["docker", "start", server_name], 8).returncode == 0,
                         "fake API restart failed")
                try:
                    result = call(["docker", "start", "--attach", worker_name], 35)
                except subprocess.TimeoutExpired:
                    worker_log = call(["docker", "logs", "--tail", "18", worker_name], 7)
                    server_log = call(["docker", "logs", "--tail", "12", server_name], 7)
                    raise UnsafePrivateNet("worker exceeded bounded stage " + str(idx+1)
                                           + " worker=" + worker_log.stderr[-800:]
                                           + " server=" + server_log.stderr[-800:])
                exited = call(["docker", "inspect", "--format",
                               "{{.State.ExitCode}}", worker_name], 7)
                if result.returncode or marker not in result.stdout.splitlines():
                    worker_log = call(["docker", "logs", "--tail", "18", worker_name], 7)
                    server_log = call(["docker", "logs", "--tail", "12", server_name], 7)
                    raise UnsafePrivateNet("worker failed stage " + str(idx + 1)
                                           + ": " + result.stderr[-500:]
                                           + ": worker=" + worker_log.stderr[-800:]
                                           + ": server=" + server_log.stderr[-800:])
                need(exited.stdout.strip() == "0" and exited.returncode == 0,
                     "worker did not terminate successfully")
                checks[check] = True
                if idx == 2:
                    checks["server_restart_preserves_active_lease"] = True
                    checks["synthetic_completion_replay_denied"] = True
            evidence = json.loads((work / "stage-journal.json").read_text())
            state = json.loads((server / "fake-assistx-fault-state.json").read_text())
            need(evidence.get("stage") == 3
                 and evidence.get("old_token_rejected") is True
                 and evidence.get("stale_claim_rejected") is True
                 and evidence.get("completion_replay_rejected") is True
                 and evidence.get("real_model_runs") == 0
                 and [state["tasks"][key]["status"] for key in (
                    "synthetic-fault-a", "synthetic-fault-b", "synthetic-fault-c")] ==
                    ["DONE", "DONE", "LOCKED"],
                 "synthetic server/worker evidence mismatch")
            network_ok(network, expected_containers=network_ids)
            checks["production_hermes_running_unchanged"] = (
                shell._image_id() == live and
                call(["docker", "inspect", "--format",
                      "{{.State.Running}}", shell.IMAGE_NAME], 7).stdout.strip() == "true")
        finally:
            # Never touch unowned networks or other Docker objects.
            for name in (worker_name, server_name):
                if name in created:
                    call(["docker", "rm", "--force", name], 15)
            for tag in reversed(created_tags):
                call(["docker", "image", "rm", tag], 12)
            if network_created:
                call(["docker", "network", "rm", network], 12)
            network_absent = (
                call(["docker", "network", "inspect", network], 7).returncode != 0)
            checks["all_disposable_docker_objects_removed"] = (
                network_absent and
                all(normal._absent_docker_object("container", name)
                    for name in (worker_name, server_name))
                and all(normal._absent_docker_object("image", tag)
                        for tag in (base, *tags)))
    need(all(checks.values()), "cross-container checks missing: " +
         ",".join(k for k, v in checks.items() if not v))
    return checks, live, image_id

def capture(physical=False):
    report = {"schema": 1, "mode": "hermes_private_internal_bridge_lease_canary",
              "host": "x1-370", "physical_canary_executed": False,
              "exact_running_image": None, "overlay_image": None,
              **{key: False for key in REQUIRED},
              "real_agent_tasks_executed": 0, "provider_requests_forwarded": 0,
              "production_deployment_authorized": False,
              "decision": "HOLD", "blockers": list(BLOCKERS)}
    if physical:
        try:
            checks, live, image_id = execute()
            report.update(checks)
            report.update(physical_canary_executed=all(checks.values()),
                          exact_running_image=live, overlay_image=image_id)
        except (UnsafePrivateNet, shell.UnsafeCanary,
                normal.UnsafeNormalStartup, OSError, ValueError, TypeError,
                subprocess.TimeoutExpired) as exc:
            print("PRIVATE_NET_CANARY_ERROR", str(exc)[-1100:], flush=True)
            report["blockers"].append("private_network_physical_canary_failed_closed")
    return report

def validate(report):
    baseline = capture()
    if not isinstance(report, dict) or set(report) != set(baseline):
        return False
    if type(report["physical_canary_executed"]) is not bool:
        return False
    if any(type(report[key]) is not bool for key in REQUIRED):
        return False
    if report["physical_canary_executed"] != all(report[key] for key in REQUIRED):
        return False
    for key in ("exact_running_image", "overlay_image"):
        value = report[key]
        if value is not None and (not isinstance(value, str)
                                  or not shell.IMAGE_ID_RE.fullmatch(value)):
            return False
    if report["physical_canary_executed"] and (
        not report["exact_running_image"] or not report["overlay_image"]):
        return False
    for key in ("schema", "mode", "host", "real_agent_tasks_executed",
                "provider_requests_forwarded", "production_deployment_authorized",
                "decision"):
        if type(report[key]) is not type(baseline[key]) or report[key] != baseline[key]:
            return False
    return isinstance(report["blockers"], list) and all(
        isinstance(k, str) for k in report["blockers"]) and all(
        k in report["blockers"] for k in BLOCKERS)

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--physical-disposable-private-network", action="store_true")
    p.add_argument("--json", action="store_true")
    arg = p.parse_args()
    record = capture(arg.physical_disposable_private_network)
    need(validate(record), "invalid restricted-network verification record")
    print(json.dumps(record, sort_keys=True, indent=2 if arg.json else None))
    if arg.physical_disposable_private_network and not record["physical_canary_executed"]:
        raise SystemExit(2)
