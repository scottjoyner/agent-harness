#!/usr/bin/env python3
"""Strict synthetic quiescent Compose validator; never authorizes deployment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

SERVICE = "hermes-quiescent-synthetic"
PROFILE = "quiescent-canary"
POLICY_KEYS = {
    "profiles", "image", "pull_policy", "entrypoint", "command",
    "restart", "user", "read_only", "network_mode", "cap_drop",
    "security_opt", "pids_limit", "mem_limit", "cpus",
}
IMAGE_TAG = re.compile(r"^og-hermes-quiescent-base:[a-z0-9-]{10,80}$")


def check(render: object, expected_image: object) -> dict[str, Any]:
    errors: set[str] = set()
    if not isinstance(render, dict) or set(render) - {"name", "services"}:
        errors.add("unknown_compose_root")
    services = render.get("services") if isinstance(render, dict) else None
    if not isinstance(services, dict) or set(services) != {SERVICE}:
        errors.add("unexpected_services")
        service = {}
    else:
        service = services[SERVICE]
        if not isinstance(service, dict):
            errors.add("service_not_a_mapping")
            service = {}
    if set(service) != POLICY_KEYS:
        errors.add("unknown_or_missing_service_keys")
    allowed_image = (isinstance(expected_image, str)
                     and IMAGE_TAG.fullmatch(expected_image) is not None)
    if not allowed_image or service.get("image") != expected_image:
        errors.add("untrusted_disposable_image_label")
    exact = {
        "profiles": [PROFILE], "pull_policy": "never",
        "entrypoint": ["/bin/sleep"], "command": ["75"],
        "restart": "no", "user": "1000:1000", "read_only": True,
        "network_mode": "none", "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 24, "mem_limit": "134217728", "cpus": 0.5,
    }
    for key, value in exact.items():
        observed = service.get(key)
        if type(value) is bool and type(observed) is not bool:
            errors.add("invalid_" + key)
        elif observed != value:
            errors.add("invalid_" + key)
    return {
        "schema": 1,
        "mode": "hermes_quiescent_synthetic_compose_guard",
        "synthetic_compose_exact_policy_pass": not errors,
        "violations": sorted(errors),
        "production_rollback_authorized": False,
        "production_deployment_authorized": False,
        "independent_claim_revocation_verified": False,
        "worker_task_dispatch_enabled": False,
        "decision": "HOLD",
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rendered-json", type=Path)
    p.add_argument("--expected-disposable-image")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if args.rendered_json is None or args.expected_disposable_image is None:
        result = check(None, None)
    else:
        try:
            f = args.rendered_json
            if not f.is_file() or f.stat().st_size > 16000 or f.is_symlink():
                raise ValueError("untrusted_rendered_config")
            result = check(json.loads(f.read_text()),
                           args.expected_disposable_image)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            result = check(None, None)
    print(json.dumps(result, sort_keys=True, indent=2 if args.json else None))
    if args.rendered_json and not result["synthetic_compose_exact_policy_pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
