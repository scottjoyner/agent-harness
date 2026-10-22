#!/usr/bin/env python3
"""One-shot, host-local, READ-ONLY AssistX deployment connectivity preflight.

Run as the ordinary fleet operator on the physical target node. Inspects
process presence, local HTTP status codes (GET /health, NO body/credentials),
and existing Tailscale Serve forwarding metadata. This is NOT an AssistX
adapter, agent scheduler, model router, authenticated probe, or release gate.
All results are HOLD. Never changes services, Tailscale, firewall or Docker.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_PS = 1024 * 1024
MAX_TAILSCALE = 8192
# Fixed localhost targets: no arbitrary URL, no redirects to remote services,
# and the operator's proxy environment is explicitly ignored.
PATHS = {
    "assistx_host_api": "http://127.0.0.1:8000/health",
    "auto_router_host_api": "http://127.0.0.1:8088/health",
    "serve_backend": "http://127.0.0.1:8081/health",
}
SERVE_TARGET = re.compile(
    r"(?m)^\|--\s+/\s+proxy\s+http://127\.0\.0\.1:(\d{1,5})\s*$"
)
PROCESSES = {
    "assistx_api": ("uvicorn", "assistx.api_router:app"),
    "auto_router": ("uvicorn", "auto_router.main_live:app"),
    "assistx_recovery": ("python", "assistx.recovery_island_agent_hardened"),
}


def _run_fixed(args: list[str], *, cap: int, timeout: float) -> str | None:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True,
            timeout=timeout, check=False)
        if result.returncode or len(result.stdout) > cap:
            return None
        return result.stdout
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None


def _processes() -> dict[str, bool | None]:
    data = _run_fixed(["ps", "-eo", "args="], cap=MAX_PS, timeout=4)
    if data is None:
        return {name: None for name in PROCESSES}
    # Do not return raw process argv or environment values. Require a
    # plausible interpreter prefix, not just coincidental substrings
    # elsewhere in a shell command or this source file.
    lines = [line.strip() for line in data.splitlines() if line.strip()]
    return {
        name: any(
            (line.startswith(("/usr/", "/srv/", "python ", "python3 ",
                              "uvicorn ", "/opt/")))
            and all(tok in line for tok in tokens)
            and "assistx_operator_probe" not in line
            for line in lines
        ) for name, tokens in PROCESSES.items()
    }


def _target_status(url: str) -> int | None:
    # Custom transport is not accepted from CLI, preventing SSRF,
    # misrouting to arbitrary fleet nodes, or proxy egress.
    if url not in PATHS.values():
        raise ValueError("host-local probe URL not on fixed allowlist")
    # Replaces the standard redirect handler rather than registering
    # another competing handler. We must NEVER follow a cross-host
    # login/proxy redirect from a local health endpoint.
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(Request(url, method="GET"), timeout=2) as resp:
            status = resp.status
    except HTTPError as error:
        status = error.code
        error.close()
    except (URLError, OSError, TimeoutError, ValueError):
        return None
    return status if type(status) is int and 100 <= status <= 599 else None


def _serve_port() -> int | None:
    data = _run_fixed(["tailscale", "serve", "status"],
                      cap=MAX_TAILSCALE, timeout=4)
    if data is None:
        return None
    match = SERVE_TARGET.search(data)
    if not match:
        return None
    port = int(match.group(1))
    return port if 1 <= port <= 65535 else None


def observe() -> dict:
    processes = _processes()
    ports = {name: _target_status(url) for name, url in PATHS.items()}
    serve = _serve_port()
    blockers = []
    if processes["assistx_api"] is not True:
        blockers.append("assistx_process_not_independently_confirmed")
    if processes["auto_router"] is not True:
        blockers.append("auto_router_process_not_independently_confirmed")
    if ports["assistx_host_api"] != 200:
        blockers.append("assistx_host_health_not_http_200")
    if ports["auto_router_host_api"] != 200:
        blockers.append("auto_router_host_health_not_http_200")
    if serve is None:
        blockers.append("tailscale_serve_target_unverified")
    if serve != 8000:
        blockers.append("tailscale_serve_not_directly_proxying_assistx_port_8000")
    if ports["serve_backend"] != 200:
        blockers.append("serve_backend_health_not_http_200")
    # Even if all connectivity checks pass, no authenticated source
    # attestation, deployment/image binding, role/owner protection or
    # verified model runtime is supplied by this diagnostic tool.
    blockers += [
        "authenticated_assistx_catalog_not_verified",
        "exact_deployed_image_and_commit_not_verified",
        "source_owner_bound_execution_not_verified",
        "independent_admission_and_revocation_custody_not_verified",
    ]
    return {
        "schema": 1,
        "mode": "assistx_host_local_readonly_deployment_probe",
        "process_presence": processes,
        "host_local_health_http_status": ports,
        "tailnet_serve_backend_port": serve,
        "read_only_http_get_only": True,
        "authenticated_catalog_verified": False,
        "exact_runtime_image_verified": False,
        "source_authoritative_transactions_verified": False,
        "independent_admission_verified": False,
        "production_deployment_authorized": False,
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
        "shell_executor_authorized": False,
        "automatic_retry_or_refund_authorized": False,
        "decision": "HOLD",
        "blockers": blockers,
    }


def validate(record: object) -> bool:
    if not isinstance(record, dict) or set(record) != {
        "schema", "mode", "process_presence", "host_local_health_http_status",
        "tailnet_serve_backend_port", "read_only_http_get_only",
        "authenticated_catalog_verified", "exact_runtime_image_verified",
        "source_authoritative_transactions_verified",
        "independent_admission_verified", "production_deployment_authorized",
        "agent_dispatch_authorized", "provider_requests_forwarded",
        "shell_executor_authorized", "automatic_retry_or_refund_authorized",
        "decision", "blockers"
    }:
        return False
    return (
        type(record["schema"]) is int and record["schema"] == 1
        and record["mode"] == "assistx_host_local_readonly_deployment_probe"
        and isinstance(record["process_presence"], dict)
        and set(record["process_presence"]) == set(PROCESSES)
        and all(
            record["process_presence"][x] is None
            or type(record["process_presence"][x]) is bool
            for x in PROCESSES
        )
        and isinstance(record["host_local_health_http_status"], dict)
        and set(record["host_local_health_http_status"]) == set(PATHS)
        and all(type(x) is int and 100 <= x <= 599 or x is None
                for x in record["host_local_health_http_status"].values())
        and (
            record["tailnet_serve_backend_port"] is None
            or type(record["tailnet_serve_backend_port"]) is int
            and 1 <= record["tailnet_serve_backend_port"] <= 65535
        )
        and record["read_only_http_get_only"] is True
        and all(record[k] is False for k in (
            "authenticated_catalog_verified", "exact_runtime_image_verified",
            "source_authoritative_transactions_verified",
            "independent_admission_verified", "production_deployment_authorized",
            "agent_dispatch_authorized", "shell_executor_authorized",
            "automatic_retry_or_refund_authorized"))
        and type(record["provider_requests_forwarded"]) is int
        and record["provider_requests_forwarded"] == 0
        and record["decision"] == "HOLD"
        and isinstance(record["blockers"], list)
        and all(isinstance(x, str) and 1 <= len(x) <= 100
                for x in record["blockers"])
        and all(reason in record["blockers"] for reason in (
            "authenticated_assistx_catalog_not_verified",
            "exact_deployed_image_and_commit_not_verified",
            "source_owner_bound_execution_not_verified",
            "independent_admission_and_revocation_custody_not_verified",
        ))
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true",
                        help="Print JSON with no credentials or response bodies")
    args = parser.parse_args()
    result = observe()
    assert validate(result)
    print(json.dumps(result, sort_keys=True, indent=2 if args.json else None))
