#!/usr/bin/env python3
"""Read-only catalog presence probe; never authenticates or starts an agent turn."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CATALOGS = {
    "kilo": ("kilo", "models"),
    "opencode": ("opencode", "models"),
    "cmdcode": ("cmd", "--list-models"),
}


def first_tokens(output: str) -> set[str]:
    """Only an exact first-column identifier counts, not descriptive prose."""
    return {line.split()[0] for line in output.splitlines() if line.split()}


def probe(roster: dict, *, which=shutil.which, runner=subprocess.run,
          timeout_s: int = 45) -> dict:
    results = {}
    with tempfile.TemporaryDirectory(prefix="og-catalog-") as scratch:
        # Never pass API tokens or provider credentials through to a probe.
        # Whitelisting is safer than trying to enumerate secret variable names.
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "HOME": scratch,
            "XDG_CONFIG_HOME": f"{scratch}/config",
            "XDG_DATA_HOME": f"{scratch}/data",
            "XDG_CACHE_HOME": f"{scratch}/cache",
            "TERM": "dumb",
            "NO_COLOR": "1",
        }
        for coder in roster.get("coders", []):
            role = coder.get("id")
            expected = coder.get("model_pin")
            status = {"requested": expected, "status": "unverified",
                      "catalog_listed": False}
            if role == "freebuff" and isinstance(expected, str) and expected:
                # BuffLINK v0.4.0 implements --list-models, but launches the
                # native Freebuff TUI and navigates its picker in a PTY.
                # Running it on an unqualified host bypasses our OS sandbox
                # gate, so never invoke it from this credential-free probe.
                if which("blink") and which("freebuff"):
                    status.update(
                        status="requires_sandbox",
                        reason="BuffLINK --list-models launches Freebuff TUI; "
                               "live model discovery requires physical sandbox admission",
                    )
                else:
                    status["reason"] = "BuffLINK bridge or native Freebuff executable missing"
            elif role not in CATALOGS or not isinstance(expected, str) or not expected:
                status["reason"] = "unsupported role or missing pin"
            else:
                executable, *args = CATALOGS[role]
                resolved = which(executable)
                if not resolved:
                    status["reason"] = "catalog executable missing"
                else:
                    try:
                        completed = runner(
                            [resolved, *args], stdin=subprocess.DEVNULL,
                            cwd=scratch, env=env, capture_output=True,
                            text=True, timeout=timeout_s,
                        )
                        if completed.returncode != 0:
                            status["reason"] = "catalog command failed"
                        elif expected in first_tokens(completed.stdout):
                            status.update(status="listed", catalog_listed=True,
                                          reason="listed only; auth and dispatch unverified")
                        else:
                            status.update(status="not_listed",
                                          reason="requested model missing from catalog")
                    except (OSError, subprocess.TimeoutExpired):
                        status["reason"] = "catalog command failed or timed out"
            results[role or "invalid"] = status
    return {"schema": 1, "catalogs": results,
            "requested_catalogs_listed": bool(results) and
            all(r["catalog_listed"] for r in results.values()),
            "model_pins_verified": False,
            "authentication_verified": False,
            "agent_dispatch_authorized": False,
            "scope": "public catalog discovery with disposable HOME; no model turns"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, default=Path(__file__).with_name("roster.json"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = probe(json.loads(args.roster.read_text(encoding="utf-8")))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for agent, details in result["catalogs"].items():
            print(f"{agent}: {details['status']} — {details['requested']}")
        print("dispatch_authorized=false")
    return 0 if result["requested_catalogs_listed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
