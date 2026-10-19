#!/usr/bin/env python3
"""Read-only verification of an extracted Ubuntu bwrap-userns-restrict profile."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import subprocess
from pathlib import Path

# apparmor-profiles 4.0.1really4.0.1-0ubuntu0.24.04.9 (amd64 / all).
# Source archive SHA256: 90b02aa006eea7702cd4e851343e469e41365dda42145a3cb035de1d6c773b8c
PROFILE_SHA256 = "11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9"
RESTRICTION = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")


def inspect(profile: Path, *, expected_digest: str = PROFILE_SHA256,
            restriction_value: str | None = None,
            which=shutil.which, runner=subprocess.run) -> dict:
    report = {
        "schema": 1,
        "observed_host": socket.gethostname(),
        "profile_digest_matches": False,
        "parser_valid": False,
        "global_userns_restriction_preserved": False,
        "candidate_qualified": False,
        "profile_loaded": False,
        "administrator_action_authorized": False,
        "agent_dispatch_authorized": False,
        "errors": [],
    }
    try:
        restriction_value = (RESTRICTION.read_text(encoding="utf-8").strip()
                             if restriction_value is None else restriction_value.strip())
        report["global_userns_restriction_preserved"] = restriction_value == "1"
    except OSError:
        report["errors"].append("Unable to read global AppArmor restriction")
    if not report["global_userns_restriction_preserved"]:
        report["errors"].append("Global unprivileged namespace restriction not enabled")
    if profile.is_symlink() or not profile.is_file():
        report["errors"].append("Candidate missing, symlinked, or not a regular file")
        return report
    try:
        report["profile_digest_matches"] = (
            hashlib.sha256(profile.read_bytes()).hexdigest() == expected_digest)
    except OSError:
        report["errors"].append("Candidate not readable")
        return report
    if not report["profile_digest_matches"]:
        report["errors"].append("Candidate SHA256 differs from pinned vendor profile")
        return report
    binary = which("apparmor_parser")
    if not binary:
        report["errors"].append("AppArmor parser unavailable")
        return report
    try:
        check = runner([binary, "-Q", "-T", str(profile)],
                       capture_output=True, text=True, timeout=15)
        report["parser_valid"] = check.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        report["errors"].append("AppArmor offline syntax parse failed or timed out")
    if not report["parser_valid"]:
        report["errors"].append("AppArmor vendor profile parser returned failure")
    report["candidate_qualified"] = (
        report["profile_digest_matches"] and report["parser_valid"]
        and report["global_userns_restriction_preserved"])
    return report


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("profile", type=Path)
    args = cli.parse_args()
    result = inspect(args.profile)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["candidate_qualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
