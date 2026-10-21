#!/usr/bin/env python3
"""Fail-closed READ-ONLY mount-risk inspection for the live Hermes adapter.

This research helper reads ONLY Docker .Mounts JSON via a fixed CLI command.
It does not load Docker Config.Env, open any mounted files, write a
container path, create a container, alter Compose, or restart a service.

A hypothetical safe *mount topology* is not an attestation of actual
Linux kernel mount enforcement, source custody, or authorization.
Every returned observation remains HOLD.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

EVIDENCE_ROOT = (
    "/media/scott/SSD_4TB/knowledge/"
    "50-Research/model-evaluation/free-first-monitor"
)
SERVICE = "assistx-hermes-adapter"
MAX_MOUNTS = 64
MAX_BYTES = 64 * 1024
FIELDS = {"Type", "Source", "Destination", "RW"}
CONSTANT_BLOCKERS = (
    "physical_write_denial_not_tested",
    "independently_custodied_evidence_head_unverified",
    "running_source_revision_not_attested",
    "source_owner_runtime_authorization_unverified",
)


class UnsafeMountMetadata(ValueError):
    pass


def _pairwise_unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise UnsafeMountMetadata("duplicate Docker mount metadata")
        result[k] = v
    return result


def _contained(root: str, path: str) -> bool:
    try:
        return os.path.commonpath((root, path)) == root
    except ValueError:
        return False


def _canonical(path: str) -> str:
    if (not isinstance(path, str) or not 1 <= len(path) <= 4096
            or not path.startswith("/") or "\x00" in path):
        raise UnsafeMountMetadata("mount requires bounded absolute source")
    return os.path.realpath(path)


def _valid_entry(obj: object) -> bool:
    return (
        isinstance(obj, dict)
        and FIELDS.issubset(obj)
        and obj["Type"] in ("bind", "volume", "tmpfs")
        and isinstance(obj["Source"], str)
        and isinstance(obj["Destination"], str)
        and obj["Destination"].startswith("/")
        and len(obj["Destination"]) <= 4096
        and "\x00" not in obj["Destination"]
        and type(obj["RW"]) is bool
    )


def parse_docker_mounts(data: bytes) -> list[dict]:
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_BYTES:
        raise UnsafeMountMetadata("Docker mount metadata missing/oversized")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairwise_unique)
    except (ValueError, UnicodeError, TypeError, OverflowError) as exc:
        raise UnsafeMountMetadata("Docker mount JSON invalid") from exc
    if (not isinstance(value, list) or not 1 <= len(value) <= MAX_MOUNTS
            or any(not _valid_entry(x) for x in value)):
        raise UnsafeMountMetadata("Docker mount list unsupported")
    return value


def assess(mounts: list[dict] | None, *, physical_docker_inspect: bool,
           docker_unavailable: bool = False) -> dict:
    """Return an operator-only HOLD evidence record with no raw mount paths."""
    invalid = (
        not isinstance(mounts, list) or not 1 <= len(mounts) <= MAX_MOUNTS
        or any(not _valid_entry(x) for x in mounts)
    )
    mounts = mounts if not invalid else []
    root = os.path.realpath(EVIDENCE_ROOT)
    if not root.startswith("/"):
        invalid = True
    hazardous = 0
    unknown = 0
    writable = 0
    binds = 0
    evidence_aliases = 0
    visible_destinations: set[str] = set()
    for mount in mounts:
        writable += int(mount["RW"])
        try:
            destination = _canonical(mount["Destination"])
            if destination in visible_destinations:
                unknown += 1
            visible_destinations.add(destination)
            if mount["Type"] != "bind":
                # Named Docker volumes and tmpfs are NOT treated as
                # authority about their backing host paths.
                continue
            binds += 1
            source = _canonical(mount["Source"])
            if not os.path.exists(source):
                unknown += 1
                continue
            related = _contained(source, root) or _contained(root, source)
            if related:
                evidence_aliases += 1
                if mount["RW"]:
                    hazardous += 1
        except (UnsafeMountMetadata, OSError, ValueError):
            unknown += 1
    topology = not invalid and binds > 0 and unknown == 0 and hazardous == 0
    blockers = []
    if invalid:
        blockers.append("mount_list_missing_or_invalid")
    if not physical_docker_inspect:
        blockers.append("mount_list_only_synthetic")
    if unknown:
        blockers.append("mount_sources_or_destinations_not_resolved")
    if hazardous:
        blockers.append("protected_evidence_reachable_via_rw_mount")
    if not binds:
        blockers.append("no_verifiable_bind_mounts")
    blockers.extend(CONSTANT_BLOCKERS)
    return {
        "schema": 1,
        "mode": "assistx_hermes_mount_topology_readonly",
        "source": (
            "unavailable_docker_inspect" if docker_unavailable else
            "live_docker_inspect" if physical_docker_inspect
            else "synthetic_review_fixture"
        ),
        "service": SERVICE,
        "docker_metadata_observed": physical_docker_inspect and not invalid,
        "total_mounts": len(mounts),
        "bind_mounts": binds,
        "writable_mounts": writable,
        "protected_evidence_ancestor_or_descendant_aliases": evidence_aliases,
        "protected_evidence_rw_aliases": hazardous,
        "unresolved_mount_source_or_destination_count": unknown,
        "mount_topology_static_check_passed": topology,
        "mount_source_paths_exported": False,
        "mounted_files_read": False,
        "physical_write_denial_verified": False,
        "external_witness_custody_verified": False,
        "source_revision_attested": False,
        "production_deployment_authorized": False,
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
        "container_recreated_or_modified": False,
        "decision": "HOLD",
        "blockers": blockers,
    }


def inspect_live() -> dict:
    """One fixed read-only Docker CLI inspect, with data/env excluded."""
    try:
        command = [
            "docker", "inspect", "--format", "{{json .Mounts}}", SERVICE,
        ]
        r = subprocess.run(command, capture_output=True,
                           timeout=6, check=False)
        if r.returncode:
            raise UnsafeMountMetadata("Docker inspect unavailable")
        mounts = parse_docker_mounts(r.stdout)
        return assess(mounts, physical_docker_inspect=True)
    except (OSError, subprocess.TimeoutExpired, UnsafeMountMetadata):
        return assess(None, physical_docker_inspect=False,
                      docker_unavailable=True)


def verify(report: object) -> bool:
    required = {
        "schema","mode","source","service","docker_metadata_observed",
        "total_mounts","bind_mounts","writable_mounts",
        "protected_evidence_ancestor_or_descendant_aliases",
        "protected_evidence_rw_aliases",
        "unresolved_mount_source_or_destination_count",
        "mount_topology_static_check_passed",
        "mount_source_paths_exported","mounted_files_read",
        "physical_write_denial_verified","external_witness_custody_verified",
        "source_revision_attested","production_deployment_authorized",
        "agent_dispatch_authorized","provider_requests_forwarded",
        "container_recreated_or_modified","decision","blockers",
    }
    if not isinstance(report, dict) or set(report) != required:
        return False
    if (type(report["schema"]) is not int or report["schema"] != 1
            or report["mode"] != "assistx_hermes_mount_topology_readonly"
            or report["source"] not in (
                "live_docker_inspect","synthetic_review_fixture",
                "unavailable_docker_inspect")
            or report["service"] != SERVICE):
        return False
    numeric = (
        "total_mounts","bind_mounts","writable_mounts",
        "protected_evidence_ancestor_or_descendant_aliases",
        "protected_evidence_rw_aliases",
        "unresolved_mount_source_or_destination_count",
    )
    if any(type(report[x]) is not int or report[x] < 0 or report[x] > MAX_MOUNTS
           for x in numeric):
        return False
    if not (report["bind_mounts"] <= report["total_mounts"]
            and report["writable_mounts"] <= report["total_mounts"]
            and report["protected_evidence_rw_aliases"] <=
                    report["protected_evidence_ancestor_or_descendant_aliases"] <=
                    report["bind_mounts"]):
        return False
    if not all(type(report[x]) is bool for x in (
        "docker_metadata_observed","mount_topology_static_check_passed",
        "mount_source_paths_exported","mounted_files_read",
        "physical_write_denial_verified","external_witness_custody_verified",
        "source_revision_attested","production_deployment_authorized",
        "agent_dispatch_authorized","container_recreated_or_modified",
    )):
        return False
    if (any(report[x] for x in (
            "mount_source_paths_exported","mounted_files_read",
            "physical_write_denial_verified","external_witness_custody_verified",
            "source_revision_attested","production_deployment_authorized",
            "agent_dispatch_authorized","container_recreated_or_modified"))
            or type(report["provider_requests_forwarded"]) is not int
            or report["provider_requests_forwarded"] != 0
            or report["decision"] != "HOLD"
            or not isinstance(report["blockers"], list)
            or any(type(x) is not str or not 1 <= len(x) <= 100
                   for x in report["blockers"])
            or not all(x in report["blockers"] for x in CONSTANT_BLOCKERS)):
        return False
    if report["mount_topology_static_check_passed"]:
        if (report["protected_evidence_rw_aliases"] != 0
                or report["unresolved_mount_source_or_destination_count"] != 0
                or report["total_mounts"] == 0):
            return False
    if report["docker_metadata_observed"]:
        if report["source"] != "live_docker_inspect" or report["total_mounts"] == 0:
            return False
    else:
        if report["source"] == "live_docker_inspect":
            return False
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json",action="store_true")
    args=ap.parse_args()
    result=inspect_live()
    if not verify(result):
        raise SystemExit("untrusted read-only Hermes mount metadata")
    print(json.dumps(result,indent=2 if args.json else None,sort_keys=True))
