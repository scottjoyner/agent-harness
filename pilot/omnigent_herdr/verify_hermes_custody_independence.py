#!/usr/bin/env python3
"""Fail-closed Hermes witness administrative separation preflight.

Default: no I/O. Opt-in: read-only SSH metadata to fixed fleet nodes.
Same-operator SSH capability proves a shared-administration RISK;
lack of SSH capability can NEVER establish independent key custody.
The script does not access signing keys, Docker or protected evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import subprocess
from typing import Any

KNOWN_HOSTS = ("lenovo", "destroyer", "xwing")
PRIVILEGED_GROUPS = frozenset(("sudo", "wheel", "docker", "lxd", "libvirt"))
NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
UID = re.compile(r"^[0-9]{1,9}$")
MAX_RESPONSE_BYTES = 2048

# Strict fixed-command observation only. No arbitrary SSH target or shell
# command comes from user input or the remote host's response.
READ_COMMAND = (
    'printf "NODE="; hostname; '
    'printf "USER="; id -un; '
    'printf "UID="; id -u; '
    'printf "GROUPS="; id -Gn'
)


def parse_remote(stdout: object) -> dict[str, Any] | None:
    if not isinstance(stdout, str) or len(stdout.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return None
    lines = stdout.strip().splitlines()
    if len(lines) != 4 or any("=" not in line for line in lines):
        return None
    try:
        data = dict(line.split("=", 1) for line in lines)
    except (ValueError, TypeError):
        return None
    if set(data) != {"NODE", "USER", "UID", "GROUPS"}:
        return None
    if (not NAME.fullmatch(data["NODE"]) or not NAME.fullmatch(data["USER"])
            or not UID.fullmatch(data["UID"])
            or len(data["GROUPS"]) > 700):
        return None
    groups = data["GROUPS"].split()
    if not groups or len(groups) != len(set(groups)):
        return None
    if not all(NAME.fullmatch(g) for g in groups):
        return None
    return {
        "observed_node": data["NODE"],
        "login_user": data["USER"],
        "uid": int(data["UID"]),
        "groups": groups,
    }


def classify(*, local_user: object, local_uid: object,
             observations: object) -> dict[str, Any]:
    risks: set[str] = set()
    details: list[dict[str, Any]] = []
    successful = 0
    shared_privileged = 0
    # This guard cannot establish independence: the observation is obtained
    # through the existing fleet operator's access path.
    if (not isinstance(local_user, str) or not NAME.fullmatch(local_user)
            or type(local_uid) is not int or local_uid < 0):
        risks.add("local_identity_untrusted")
    if not isinstance(observations, dict):
        risks.add("candidate_inventory_incomplete")
        observations = {}
    elif set(observations) != set(KNOWN_HOSTS):
        risks.add("candidate_inventory_incomplete")

    for target in KNOWN_HOSTS:
        observed = observations.get(target)
        recognized = (
            isinstance(observed, dict) and
            set(observed) == {"observed_node", "login_user", "uid", "groups"} and
            isinstance(observed["observed_node"], str)
            and NAME.fullmatch(observed["observed_node"]) is not None
            and isinstance(observed["login_user"], str)
            and NAME.fullmatch(observed["login_user"]) is not None
            and type(observed["uid"]) is int and observed["uid"] >= 0
            and isinstance(observed["groups"], list)
            and all(isinstance(group, str) and NAME.fullmatch(group) is not None
                    for group in observed["groups"])
        )
        if not recognized:
            risks.add("unqualified_remote_principal")
            details.append({"candidate": target, "ssh_verified": False,
                            "same_account_observed": None,
                            "privileged_group_membership_observed": None,
                            "independent_admin_custody_verified": False})
            continue
        successful += 1
        same_user = observed["login_user"] == local_user
        privileged = bool(PRIVILEGED_GROUPS.intersection(observed["groups"]))
        if same_user:
            risks.add("same_operator_login_on_candidate")
        if same_user and privileged:
            risks.add("shared_privileged_principal_observed")
            shared_privileged += 1
        if privileged:
            risks.add("remote_privileged_group_membership")
        details.append({
            "candidate": target,
            "ssh_verified": True,
            "same_account_observed": same_user,
            "privileged_group_membership_observed": privileged,
            "independent_admin_custody_verified": False,
        })

    return {
        "schema": 1,
        "mode": "hermes_external_custody_principal_readonly_preflight",
        "candidate_count": len(KNOWN_HOSTS),
        "ssh_observations_verified": successful,
        "shared_privileged_principals_observed": shared_privileged,
        "candidates": details,
        "violations": sorted(risks),
        "independent_administrator_verified": False,
        "independently_pinned_signer_custody_verified": False,
        "rollback_resistant_external_head_verified": False,
        "source_owner_claim_authority_verified": False,
        "production_no_dispatch_authorized": False,
        "production_deployment_authorized": False,
        "decision": "HOLD",
    }


def audit_physical_readonly() -> dict[str, Any]:
    if os.uname().nodename != "x1-370" or os.geteuid() == 0:
        raise ValueError("nonroot_x1_only")
    identity = pwd.getpwuid(os.getuid())
    observations: dict[str, object] = {}
    for target in KNOWN_HOSTS:
        command = [
            "ssh", "-T",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=6",
            "-o", "ConnectionAttempts=1",
            "-o", "NumberOfPasswordPrompts=0",
            "--", target, READ_COMMAND,
        ]
        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  check=False, timeout=10)
            observations[target] = (parse_remote(proc.stdout)
                                    if proc.returncode == 0 else None)
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            observations[target] = None
    return classify(local_user=identity.pw_name, local_uid=os.getuid(),
                    observations=observations)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-ssh-readonly", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.audit_ssh_readonly:
        try:
            result = audit_physical_readonly()
        except (OSError, ValueError, TypeError, KeyError):
            result = classify(local_user=None, local_uid=None,
                              observations=None)
            result["violations"] = sorted(set(result["violations"]) |
                                          {"physical_audit_failed_closed"})
    else:
        result = classify(local_user=None, local_uid=None, observations=None)
        result["violations"] = sorted(set(result["violations"]) |
                                      {"explicit_opt_in_required"})
    print(json.dumps(result, indent=2 if args.json else None, sort_keys=True))
    # No script outcome grants production readiness.
    if args.audit_ssh_readonly and result["ssh_observations_verified"] != len(KNOWN_HOSTS):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
