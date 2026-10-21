#!/usr/bin/env python3
"""One-shot read-only AssistX + Auto-Router inventory/auth-boundary bridge.

The only output is a bounded, sanitized HOLD manifest for Herdr/Omnigent
operators. No model IDs or prompts are exported, and the bridge cannot
connect to model-inference or task-execution endpoints. All destinations
are exact fixed loopback URLs. A successful model LIST is *not* evidence
of a physically loaded model, source-owned effect, quota, or auth.

No credentials, provider/agent invocation, network listening, changes
to Tailscale or Docker, or deployment permission. Compatible Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)

from assistx_operator_probe import observe as observe_host
from assistx_operator_probe import validate as validate_host

URLS = {
    "assistx_openapi": "http://127.0.0.1:8000/openapi.json",
    "router_openapi": "http://127.0.0.1:8088/openapi.json",
    "assistx_auth": "http://127.0.0.1:8000/api/v1/auth/whoami",
    "assistx_catalog": "http://127.0.0.1:8000/api/v1/runtime/catalog",
    "router_inventory": "http://127.0.0.1:8088/v1/models",
}
MAX_READ = {
    "assistx_openapi": 2_000_000,
    "router_openapi": 500_000,
    "router_inventory": 65_536,
}
MAX_MODELS = 128
ALL_UNVERIFIED = (
    "authenticated_catalog_access_verified",
    "source_owner_bound_execution_verified",
    "per_user_model_quota_verified",
    "loaded_model_residency_verified",
    "image_source_provenance_verified",
    "independent_replay_revocation_custody_verified",
    "production_deployment_authorized",
    "agent_dispatch_authorized",
    "model_egress_authorized",
    "automatic_retry_or_refund_authorized",
    "shell_executor_authorized",
)


class UnsafeBridgeEvidence(ValueError):
    pass


def _unique_fields(pairs):
    d = {}
    for key, val in pairs:
        if key in d:
            raise UnsafeBridgeEvidence("duplicate JSON member")
        d[key] = val
    return d


def _parse_json(content: bytes, limit: int) -> dict:
    if not isinstance(content, bytes) or len(content) > limit:
        raise UnsafeBridgeEvidence("response missing or over byte budget")
    try:
        d = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_fields)
    except (ValueError, UnicodeDecodeError, TypeError, OverflowError) as exc:
        raise UnsafeBridgeEvidence("malformed remote JSON") from exc
    if not isinstance(d, dict):
        raise UnsafeBridgeEvidence("unexpected JSON type")
    return d


class DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_status_or_json(key: str) -> tuple[int | None, dict | None]:
    if key not in URLS:
        raise UnsafeBridgeEvidence("nonallowlisted bridge destination")
    opener = build_opener(ProxyHandler({}), DenyRedirect())
    request = Request(
        URLS[key], method="GET",
        headers={"Accept": "application/json", "User-Agent": "fleet-readonly/1"})
    try:
        with opener.open(request, timeout=3) as response:
            code = response.status
            if type(code) is not int or code != 200:
                return code, None
            if key not in MAX_READ:
                # Only 401 from protected routes is valid; never read
                # or emit identity/catalog JSON from an unauthenticated
                # endpoint that accidentally returns HTTP 200.
                return 200, None
            bound = MAX_READ[key]
            raw = response.read(bound + 1)
            return 200, _parse_json(raw, bound)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        return (code if type(code) is int else None), None
    except (URLError, OSError, TimeoutError, ValueError, UnicodeError):
        return None, None


def _check_identity(
        response: dict | None, *, title: str,
        required_methods: dict[str, str],
        protected_get: tuple[str, ...] = (),
) -> bool:
    if not isinstance(response, dict):
        return False
    info = response.get("info")
    if not isinstance(info, dict) or info.get("title") != title:
        return False
    paths = response.get("paths")
    if not isinstance(paths, dict):
        return False
    schemes = response.get("components", {})
    if not isinstance(schemes, dict):
        return False
    schemes = schemes.get("securitySchemes", {})
    if not isinstance(schemes, dict):
        return False
    for route, method in required_methods.items():
        if (route not in paths or not isinstance(paths[route], dict)
                or not isinstance(paths[route].get(method), dict)):
            return False
    for route in protected_get:
        method = paths[route]["get"]
        security = method.get("security")
        if (not isinstance(security, list) or not security
                or not any(
                    isinstance(entry, dict) and any(
                        s in schemes and
                        isinstance(schemes[s], dict) and
                        schemes[s].get("type") in ("http", "apiKey")
                        for s in entry)
                    for entry in security)):
            return False
    return True


def _inventory_count(response: dict | None) -> int | None:
    if (not isinstance(response, dict)
            or response.get("object") != "list"
            or not isinstance(response.get("data"), list)):
        return None
    rows = response["data"]
    if len(rows) > MAX_MODELS:
        return None
    if any(
        not isinstance(row, dict)
        or row.get("object") != "model"
        or not isinstance(row.get("id"), str)
        or not 1 <= len(row["id"]) <= 256
        for row in rows
    ):
        return None
    # Critically: no model identifiers, provider labels, URLs, or
    # available/loaded classification are exported.
    return len(rows)


def capture() -> dict:
    host = observe_host()
    valid_host = validate_host(host)
    observed = {k: _request_status_or_json(k) for k in URLS}
    auth_code, _ = observed["assistx_auth"]
    catalog_code, _ = observed["assistx_catalog"]
    aid = _check_identity(
        observed["assistx_openapi"][1], title="AssistX API & UI",
        required_methods={
            "/health": "get",
            "/api/v1/auth/whoami": "get",
            "/api/v1/runtime/catalog": "get"},
        protected_get=("/api/v1/auth/whoami","/api/v1/runtime/catalog"))
    rid = _check_identity(
        observed["router_openapi"][1], title="auto-router",
        required_methods={"/health": "get", "/v1/models": "get"})
    models = _inventory_count(observed["router_inventory"][1])
    protected = auth_code in (401, 403) and catalog_code in (401, 403)
    blockers = []
    if not valid_host:
        blockers.append("host_preflight_invalid")
    if host.get("host_local_health_http_status", {}).get("assistx_host_api") != 200:
        blockers.append("assistx_unreachable_from_operator_namespace")
    if host.get("host_local_health_http_status", {}).get("auto_router_host_api") != 200:
        blockers.append("router_unreachable_from_operator_namespace")
    if not aid:
        blockers.append("assistx_openapi_or_required_auth_contract_unverified")
    if not rid:
        blockers.append("router_openapi_identity_or_inventory_contract_unverified")
    if not protected:
        blockers.append("protected_assistx_routes_not_denying_anonymous")
    if models is None:
        blockers.append("router_advertised_model_list_unavailable_or_malformed")
    blockers.extend((
        "source_authenticated_catalog_unverified",
        "advertised_models_are_not_loaded_model_attestation",
        "source_owner_quota_and_execution_receipts_missing",
        "exact_running_source_revision_unverified",
        "independent_resource_revocation_custody_missing",
    ))
    return {
        "schema": 1,
        "mode": "assistx_herdr_readonly_catalog_bridge",
        "service_identity": {
            "assistx_openapi_title_verified": aid,
            "router_openapi_title_verified": rid,
        },
        "uncredentialed_boundary": {
            "assistx_whoami_status": auth_code,
            "assistx_runtime_catalog_status": catalog_code,
            "anonymous_denial_observed": protected,
        },
        "router_inventory": {
            "advertised_model_count": models,
            "available_vs_loaded_independently_verified": False,
            "model_identifiers_exported": False,
        },
        "host_observation_valid": valid_host,
        "host_unauthenticated_health": host.get("host_local_health_http_status")
             if valid_host else None,
        **{key: False for key in ALL_UNVERIFIED},
        "provider_requests_forwarded": 0,
        "decision": "HOLD",
        "blockers": blockers,
    }


def verify(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    expected={
        "schema","mode","service_identity","uncredentialed_boundary",
        "router_inventory","host_observation_valid","host_unauthenticated_health",
        *ALL_UNVERIFIED,
        "provider_requests_forwarded","decision","blockers",
    }
    if set(record) != expected:
        return False
    identity=record["service_identity"]
    boundary=record["uncredentialed_boundary"]
    inventory=record["router_inventory"]
    if (type(record["schema"]) is not int or record["schema"] != 1
            or record["mode"] != "assistx_herdr_readonly_catalog_bridge"
            or not isinstance(identity, dict)
            or set(identity) != {
                "assistx_openapi_title_verified", "router_openapi_title_verified"}
            or not all(type(v) is bool for v in identity.values())
            or not isinstance(boundary, dict)
            or set(boundary) != {
                "assistx_whoami_status","assistx_runtime_catalog_status",
                "anonymous_denial_observed"}
            or type(boundary["anonymous_denial_observed"]) is not bool
            or not all(
                v is None or type(v) is int and 100 <= v <= 599
                for k,v in boundary.items() if k != "anonymous_denial_observed"
            )
            or not isinstance(inventory, dict)
            or set(inventory) != {
                "advertised_model_count",
                "available_vs_loaded_independently_verified",
                "model_identifiers_exported"}
            or not (inventory["advertised_model_count"] is None or
                type(inventory["advertised_model_count"]) is int and
                0 <= inventory["advertised_model_count"] <= MAX_MODELS)
            or inventory["available_vs_loaded_independently_verified"] is not False
            or inventory["model_identifiers_exported"] is not False
            or type(record["host_observation_valid"]) is not bool
            or record["host_unauthenticated_health"] is not None
            and (not isinstance(record["host_unauthenticated_health"],dict)
                or set(record["host_unauthenticated_health"]) != {
                    "assistx_host_api","auto_router_host_api","serve_backend"})
            or any(record[k] is not False for k in ALL_UNVERIFIED)
            or type(record["provider_requests_forwarded"]) is not int
            or record["provider_requests_forwarded"] != 0
            or record["decision"] != "HOLD"
            or not isinstance(record["blockers"],list)
            or not all(type(v) is str and 1 <= len(v) <= 100
                       for v in record["blockers"])):
        return False
    for required in (
        "source_authenticated_catalog_unverified",
        "advertised_models_are_not_loaded_model_attestation",
        "source_owner_quota_and_execution_receipts_missing",
        "exact_running_source_revision_unverified",
        "independent_resource_revocation_custody_missing"):
        if required not in record["blockers"]:
            return False
    if (boundary["anonymous_denial_observed"] is True
        and (boundary["assistx_whoami_status"] not in (401,403)
             or boundary["assistx_runtime_catalog_status"] not in (401,403))):
        return False
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = capture()
    if not verify(report):
        raise SystemExit("invalid read-only AssistX evidence")
    print(json.dumps(report, sort_keys=True, indent=2 if args.json else None))
