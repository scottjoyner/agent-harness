#!/usr/bin/env python3
"""Read-only, offline prereq/roster inspection. Does not start agents."""
from __future__ import annotations

import argparse
import json
import shutil
import socket
from pathlib import Path

BINARIES = {
    "claude": ("claude",),
    "codex": ("codex",),
    "kilo": ("kilo",),
    "opencode": ("opencode",),
    "freebuff": ("blink", "freebuff"),
    "cmdcode": ("cmd-acp",),
}
CORE = ("git", "python3", "node", "tmux", "omnigent", "herdr")


def inspect(config: dict, which=shutil.which, hostname: str | None = None) -> dict:
    """No network, shell execution, credentials, config writes, or installs."""
    observed_host = hostname if hostname is not None else socket.gethostname()
    policy = config.get("policy", {})
    failures = []
    if config.get("schema") != 1:
        failures.append("Unsupported roster schema")
    if config.get("pilot_host") != "lenovo":
        failures.append("Pilot host must remain lenovo")
    if "lenovo" not in observed_host.lower():
        failures.append(f"Wrong execution host: {observed_host} is not Lenovo")
    for flag in ("public_tunnel", "auto_update", "production_writes", "deployment"):
        if policy.get(flag) is not False:
            failures.append(f"Unsafe/missing deny-by-default policy: {flag}")
    if policy.get("merge") != "manual_only":
        failures.append("Merge must be manual_only")
    if policy.get("max_parallel_coders") not in (1, 2):
        failures.append("Pilot permits one or two concurrent coders")

    orchestrator = config.get("orchestrator", {})
    reviewer = config.get("reviewer", {})
    coders = config.get("coders", [])
    if orchestrator.get("id") != "claude" or orchestrator.get("model_pin") is not None:
        failures.append("Claude orchestrator must use CLI subscription selection, not an explicit model pin")
    if reviewer.get("id") != "codex":
        failures.append("Reviewer must be Codex")
    if not isinstance(coders, list) or not coders:
        failures.append("At least one coder required")
        coders = []
    ids = [c.get("id") for c in coders if isinstance(c, dict)]
    if len(set(ids)) != len(ids):
        failures.append("Duplicate coder")
    if ids != ["kilo", "opencode", "freebuff", "cmdcode"]:
        failures.append("Coder order/identities changed")

    model_pins = {}
    for role in [*coders, reviewer]:
        if not isinstance(role, dict):
            failures.append("Malformed role")
            continue
        agent = role.get("id")
        pin = role.get("model_pin")
        if agent not in BINARIES or not isinstance(pin, str) or not pin.strip():
            failures.append(f"Missing/invalid model pin for {agent}")
        else:
            model_pins[agent] = pin
    required = dict((name, (name,)) for name in CORE)
    for agent in ("claude", "codex", *ids):
        if agent in BINARIES:
            required[agent] = BINARIES[agent]
    binary_status = {name: {binary: bool(which(binary)) for binary in choices}
                     for name, choices in required.items()}
    missing = [f"{name}: {', '.join(b for b, found in states.items() if not found)}"
               for name, states in binary_status.items() if not all(states.values())]
    return {
        "target": config.get("pilot_host"),
        "observed_host": observed_host,
        "configuration_valid": not failures,
        "bootstrap_ready": not failures and not missing,
        "dispatch_ready": False,
        "model_pins_verified": False,
        "review_independence_verified": False,
        "model_pins_requested": model_pins,
        "cli_status": binary_status,
        "missing_cli": missing,
        "blockers": failures + missing + [
            "Live vendor/model/quota/auth and real cross-vendor review probes required",
            "OS sandbox, repo permissions, and branch protections require physical proof"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, default=Path(__file__).with_name("roster.json"))
    parser.add_argument("--json", action="store_true", help="Machine-readable report")
    args = parser.parse_args()
    report = inspect(json.loads(args.roster.read_text(encoding="utf-8")))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"bootstrap_ready={report['bootstrap_ready']} dispatch_ready={report['dispatch_ready']}")
        for blocker in report["blockers"]:
            print(f"BLOCKED: {blocker}")
    return 0 if report["bootstrap_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
