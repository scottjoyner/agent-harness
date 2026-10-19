#!/usr/bin/env python3
"""Produce a sanitized read-only Fleet Commander qualification receipt.

This report summarizes physical gate evidence. It does NOT grant runtime
permissions or replace independent authorization, signing, or admission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

INPUTS = ("preflight", "resource", "sandbox", "catalog", "apparmor",
          "egress", "git", "integrated", "lease")
GATES = {
    "preflight": ("configuration_valid", "bootstrap_ready"),
    "resource": ("resource_prefilter_passed",),
    "sandbox": ("strict_isolation_passed",),
    "catalog": ("requested_catalogs_listed",),
    "apparmor": ("candidate_qualified", "profile_loaded"),
    "egress": (
        "synthetic_socket_verified", "broker_peer_uid_verified",
        "sandbox_direct_network_denied", "sandbox_host_secret_unreadable",
    ),
    "git": (
        "isolated_commit_verified", "seed_unchanged",
        "clone_clean", "outside_guard_unchanged", "direct_network_denied",
    ),
    "integrated": (
        "integrated_synthetic_passed", "broker_denial_verified",
        "isolated_git_verified", "host_state_unchanged",
        "direct_network_denied", "sandbox_capabilities_dropped",
        "sandbox_privilege_escalation_denied",
    ),
    "lease": (
        "physical_lease_checks_passed", "replay_denied",
        "cross_owner_denied", "quota_denied", "revocation_denied",
        "epoch_denied", "hmac_forgery_denied", "scope_model_mismatch_denied",
        "forbidden_operations_denied", "broker_peer_uid_matched",
        "sandbox_direct_network_denied", "host_signing_key_unreadable",
        "host_key_unchanged",
    ),
}


def create_receipt(evidence: dict[str, dict], *, timestamp: str) -> dict:
    gates = {}
    for category in INPUTS:
        source = evidence.get(category)
        gates[category] = (
            isinstance(source, dict)
            and all(source.get(field) is True for field in GATES[category])
        )
    # A green egress-transport gate is deliberately a deny-only
    # transport witness. No real provider routes or model credentials.
    egress = evidence.get("egress", {})
    if not isinstance(egress, dict) or not (
        egress.get("mode") == "deny_only_unix_socket_fixture"
        and type(egress.get("provider_requests_forwarded")) is int
        and egress["provider_requests_forwarded"] == 0
        and egress.get("model_egress_authorized") is False
        and egress.get("agent_dispatch_authorized") is False
    ):
        gates["egress"] = False
    git = evidence.get("git", {})
    if not isinstance(git, dict) or not (
        git.get("remote_push_attempted") is False
        and git.get("used_real_repository") is False
        and git.get("agent_dispatch_authorized") is False
    ):
        gates["git"] = False
    integrated = evidence.get("integrated", {})
    if not isinstance(integrated, dict) or not (
        integrated.get("mode") == "deny_only_integrated_synthetic_canary"
        and type(integrated.get("provider_requests_forwarded")) is int
        and integrated["provider_requests_forwarded"] == 0
        and integrated.get("remote_push_attempted") is False
        and integrated.get("used_real_repository") is False
        and integrated.get("agent_dispatch_authorized") is False
    ):
        gates["integrated"] = False
    lease = evidence.get("lease", {})
    if not isinstance(lease, dict) or not (
        lease.get("mode") == "synthetic_hmac_lease_deny_only"
        and type(lease.get("provider_requests_forwarded")) is int
        and lease["provider_requests_forwarded"] == 0
        and lease.get("model_egress_authorized") is False
        and lease.get("agent_dispatch_authorized") is False
        and lease.get("used_real_repository") is False
    ):
        gates["lease"] = False
    origin = evidence.get("preflight", {})
    if not isinstance(origin, dict) or "lenovo" not in str(
        origin.get("observed_host", "")).lower():
        gates["preflight"] = False
    # Reject mixed-host evidence (e.g. Destroyer passing isolation while
    # Lenovo passes memory). Hostnames are observational labels, NOT secure
    # attestations; independently signed per-host custody is still required.
    expected_host = origin.get("observed_host") if isinstance(origin, dict) else None
    for category in INPUTS:
        source = evidence.get(category)
        if (not isinstance(source, dict) or
                not isinstance(expected_host, str) or
                source.get("observed_host") != expected_host):
            gates[category] = False
    # Even all-green machine predicates are NOT sufficient for runtime
    # authentication, quota, branch custody, vendor independence, and egress.
    return {
        "schema": 1,
        "created_at_utc": timestamp,
        "target": "lenovo",
        "gate_outcomes": gates,
        "machine_checks_pass": all(gates.values()),
        "credentials_verified": False,
        "model_pins_verified": False,
        "quota_and_pricing_verified": False,
        "agent_egress_approved": False,
        "independent_review_verified": False,
        "agent_dispatch_authorized": False,
        "decision": "HOLD",
        "reason": "Physical checks cannot substitute for auth, model, quota, "
                  "egress, and independently approved runtime admission",
    }


def load_snapshot(path: Path) -> tuple[dict, str]:
    """Use one no-follow descriptor for both JSON and evidence digest.

    This is a local filesystem-consistency check, not a signature or a
    tamper-proof external witness.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Evidence path cannot be opened safely") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 64 * 1024:
            raise ValueError("Evidence must be a bounded regular file")
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(64 * 1024 + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > 64 * 1024 or (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns
        ) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns
        ) or len(raw) != after.st_size:
            raise ValueError("Evidence changed during read or exceeded size limit")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Evidence must be UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Evidence must be a JSON object")
    return data, hashlib.sha256(raw).hexdigest()


def load_file(path: Path) -> dict:
    return load_snapshot(path)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for kind in INPUTS:
        parser.add_argument("--" + kind, type=Path, required=True)
    args = parser.parse_args()
    evidence = {}
    hashes = {}
    for kind in INPUTS:
        path = getattr(args, kind)
        evidence[kind], hashes[kind] = load_snapshot(path)
    receipt = create_receipt(
        evidence, timestamp=datetime.now(timezone.utc).isoformat())
    receipt["evidence_sha256"] = hashes
    print(json.dumps(receipt, sort_keys=True, indent=2))
    return 0 if receipt["agent_dispatch_authorized"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
