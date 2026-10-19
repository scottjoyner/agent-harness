#!/usr/bin/env python3
"""Produce a sanitized read-only Fleet Commander qualification receipt.

This report summarizes physical gate evidence. It does NOT grant runtime
permissions or replace independent authorization, signing, or admission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

INPUTS = ("preflight", "resource", "sandbox", "catalog", "apparmor",
          "egress", "git")
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
    origin = evidence.get("preflight", {})
    if not isinstance(origin, dict) or "lenovo" not in str(
        origin.get("observed_host", "")).lower():
        gates["preflight"] = False
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


def load_file(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError("Evidence file must be a regular, non-symlinked file")
    if path.stat().st_size > 64 * 1024:
        raise ValueError("Evidence file unexpectedly large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Evidence must be a JSON object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for kind in INPUTS:
        parser.add_argument("--" + kind, type=Path, required=True)
    args = parser.parse_args()
    evidence = {}
    hashes = {}
    for kind in INPUTS:
        path = getattr(args, kind)
        evidence[kind] = load_file(path)
        hashes[kind] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = create_receipt(
        evidence, timestamp=datetime.now(timezone.utc).isoformat())
    receipt["evidence_sha256"] = hashes
    print(json.dumps(receipt, sort_keys=True, indent=2))
    return 0 if receipt["agent_dispatch_authorized"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
