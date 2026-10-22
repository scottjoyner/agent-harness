#!/usr/bin/env python3
"""Deny-only static source audit of AssistX Hermes claim admission surfaces.

Never connects to API/Neo4j, imports application code, or changes runtime.
A source PASS would not establish production authorization or live-image parity.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
from typing import Any

LIVE_ROOT = Path("/home/scott/git/auto-assist-deploy-20261016/src/assistx")
SOURCE_PATHS = {
    "api": "api.py",
    "claims": "strict_claims.py",
    "hermes": "agents/hermes_agent_adapter.py",
    "database": "neo4j_client.py",
    "executor": "executor_security.py",
}
MAX_SOURCE_BYTES = 320_000


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next((n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == name), None)


def _getenv_default(func: ast.AST | None, key: str) -> str | None:
    if func is None:
        return None
    for n in ast.walk(func):
        if not isinstance(n, ast.Call):
            continue
        if not (isinstance(n.func, ast.Attribute) and n.func.attr == "getenv"):
            continue
        if (len(n.args) >= 2 and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == key
                and isinstance(n.args[1], ast.Constant)
                and isinstance(n.args[1].value, str)):
            return n.args[1].value
    return None


def _module_env_default(tree: ast.Module, assignment: str, key: str) -> str | None:
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Name) and t.id == assignment for t in targets):
                return _getenv_default(n, key)
    return None


def _route(fn: ast.AST | None, verb: str, endpoint: str) -> bool:
    if fn is None:
        return False
    for d in fn.decorator_list:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr == verb and d.args
                and isinstance(d.args[0], ast.Constant)
                and d.args[0].value == endpoint):
            return True
    return False


def _max_limit(fn: ast.AST | None) -> int | None:
    if fn is None:
        return None
    for default in fn.args.defaults:
        if not isinstance(default, ast.Call):
            continue
        if not (isinstance(default.func, ast.Name) and default.func.id == "Query"):
            continue
        for kw in default.keywords:
            if kw.arg == "le" and isinstance(kw.value, ast.Constant):
                return kw.value.value if type(kw.value.value) is int else None
    return None


def _text_constants(fn: ast.AST | None) -> str:
    if fn is None:
        return ""
    return "\n".join(
        n.value for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


def _arg_names(fn: ast.AST | None) -> set[str]:
    if fn is None:
        return set()
    return {x.arg for x in fn.args.args + fn.args.kwonlyargs}


def _returned_keys(fn: ast.AST | None) -> set[str]:
    result: set[str] = set()
    if fn is None:
        return result
    for ret in ast.walk(fn):
        if not isinstance(ret, ast.Return) or not isinstance(ret.value, ast.Dict):
            continue
        result |= {key.value for key in ret.value.keys
                   if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    return result


def analyze_sources(sources: object) -> dict[str, Any]:
    flags: set[str] = set()
    observations = {
        "list_route_present": False,
        "list_limit_cap": None,
        "list_has_cursor_input": False,
        "list_returns_next_cursor": False,
        "list_has_snapshot_input": False,
        "list_has_complete_total": False,
        "claim_status_is_per_task": False,
        "default_hermes_agent_id": None,
        "default_strict_claim_mode": None,
        "default_required_agents": None,
        "db_heartbeat_permits_missing_claim_id": None,
        "db_complete_permits_missing_claim_id": None,
    }
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_PATHS):
        flags.add("source_inventory_missing")
    else:
        try:
            if not all(isinstance(v, str) and len(v) <= MAX_SOURCE_BYTES
                       for v in sources.values()):
                raise ValueError("untrusted_source_bytes")
            trees = {k: ast.parse(v) for k, v in sources.items()}
            listing = _function(trees["api"], "api_list_tasks")
            observations["list_route_present"] = _route(listing, "get", "/api/tasks")
            observations["list_limit_cap"] = _max_limit(listing)
            names = _arg_names(listing)
            returns = _returned_keys(listing)
            observations["list_has_cursor_input"] = "cursor" in names
            observations["list_returns_next_cursor"] = "next_cursor" in returns
            observations["list_has_snapshot_input"] = (
                "snapshot" in names or "generation" in names)
            observations["list_has_complete_total"] = (
                "total_count" in returns and "snapshot_id" in returns)
            observations["claim_status_is_per_task"] = _route(
                _function(trees["executor"], "claim_status"), "get",
                "/claims/{task_id}/status")
            observations["default_hermes_agent_id"] = _module_env_default(
                trees["hermes"], "AGENT_ID", "HERMES_AGENT_ID")
            observations["default_strict_claim_mode"] = _getenv_default(
                _function(trees["claims"], "_mode"),
                "ASSISTX_REQUIRE_WORKER_CLAIM_ID")
            observations["default_required_agents"] = _getenv_default(
                _function(trees["claims"], "_required_agents"),
                "ASSISTX_CLAIM_REQUIRED_AGENTS")
            for method, name in (
                ("heartbeat_task", "db_heartbeat_permits_missing_claim_id"),
                ("complete_task", "db_complete_permits_missing_claim_id"),
            ):
                fn = _function(trees["database"], method)
                sql = _text_constants(fn)
                observations[name] = (
                    "($claim_id IS NULL OR t.claim_id=$claim_id)" in sql
                    or "coalesce(t.execution_attempt, 0)=0" in sql
                ) if fn is not None else None
        except (SyntaxError, ValueError, TypeError, MemoryError):
            flags.add("source_parse_failed_closed")

    if not observations["list_route_present"]:
        flags.add("task_listing_route_unqualified")
    if (observations["list_limit_cap"] is not None
            and not all(observations[k] for k in (
                "list_has_cursor_input","list_returns_next_cursor",
                "list_has_snapshot_input","list_has_complete_total"))):
        flags.add("bounded_task_page_without_complete_snapshot_proof")
    if not (observations["list_has_cursor_input"]
            and observations["list_returns_next_cursor"]
            and observations["list_has_snapshot_input"]
            and observations["list_has_complete_total"]):
        flags.add("authoritative_claim_inventory_not_available_from_list_route")
    if observations["default_hermes_agent_id"] == "hermes-local":
        required = (observations["default_required_agents"] or "").split(",")
        if (observations["default_strict_claim_mode"] != "all"
                and "hermes-local" not in [a.strip() for a in required]):
            flags.add("default_hermes_agent_not_required_to_present_claim_id")
    else:
        flags.add("hermes_identity_source_unqualified")
    for method in ("db_heartbeat_permits_missing_claim_id",
                   "db_complete_permits_missing_claim_id"):
        if observations[method] is not False:
            flags.add(method)
    return {
        "schema": 1,
        "mode": "hermes_source_claim_authority_static_readonly_preflight",
        "observed": observations,
        "violations": sorted(flags),
        "real_claim_census_completeness_verified": False,
        "live_api_image_source_parity_verified": False,
        "production_hermes_strict_fencing_verified": False,
        "real_resource_effects_verified": False,
        "independent_external_custody_verified": False,
        "production_no_dispatch_authorized": False,
        "production_deployment_authorized": False,
        "decision": "HOLD",
    }


def physical_readonly() -> dict[str, Any]:
    if os.uname().nodename != "x1-370" or os.geteuid() == 0:
        raise ValueError("nonroot_x1_only")
    sources = {}
    for k, rel in SOURCE_PATHS.items():
        p = LIVE_ROOT / rel
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("untrusted_source_path")
        sources[k] = p.read_text(encoding="utf-8")
    return analyze_sources(sources)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-source-readonly", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.audit_source_readonly:
        try:
            report = physical_readonly()
        except (OSError, ValueError, TypeError, UnicodeError):
            report = analyze_sources(None)
            report["violations"] = sorted(set(report["violations"]) |
                                          {"physical_source_unavailable"})
    else:
        report = analyze_sources(None)
        report["violations"] = sorted(set(report["violations"]) |
                                      {"explicit_opt_in_required"})
    print(json.dumps(report, sort_keys=True, indent=2 if args.json else None))
    # A source audit can NEVER issue a production authorization token.
    if args.audit_source_readonly and report["violations"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
