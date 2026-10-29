"""Version-pinned, evidence-preserving Terminal-Bench 4 runner for Harbor + Pi.

Runs only Harbor's official tasks/verifiers; never assigns its own pass/fail score.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DATASET = "terminal-bench/terminal-bench@4.0.0"
DEFAULT_HARBOR = "harbor"
DEFAULT_AGENT = "pi"
GUARDED_AGENT = "tb4.pi_agent:GuardedPi"
ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / ".tb4-runs"
PI_PROMPT = ROOT / "tb4" / "pi-task-prompt.md"


def normalize_endpoint(value: str) -> str:
    """Check custom model endpoint; never pass credentials in URL or logs."""
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("Endpoint must be an absolute HTTP(S) URL")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("Endpoint URL cannot include embedded credentials, query, or fragment")
    if url.scheme == "http":
        host = url.hostname
        try:
            ip = ipaddress.ip_address(host)
            private = ip.is_private or (ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"))
        except ValueError:
            private = host in {"localhost", "xwing", "joyner", "x1-370"} or host.endswith(".ts.net")
        if not private:
            raise ValueError("Unencrypted HTTP is supported only for private fleet endpoints")
    return value.rstrip("/")


def make_command(args: argparse.Namespace, endpoint_override: str | None = None) -> list[str]:
    if args.tasks < 1 or args.tasks > 66 or args.attempts < 1 or args.attempts > 5:
        raise ValueError("Require 1-66 tasks and 1-5 attempts")
    if args.concurrency < 1 or args.concurrency > 4:
        raise ValueError("Concurrency must be between 1 and 4")
    if args.max_turns < 1 or args.max_turns > 256:
        raise ValueError("max-turns must be between 1 and 256")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", args.job_name):
        raise ValueError("Invalid job name")
    endpoint = normalize_endpoint(endpoint_override or args.endpoint)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", args.key_env):
        raise ValueError("key-env must be a safe environment variable name")
    cmd = [str(args.harbor), "run", "-d", DATASET,
           "--agent", (GUARDED_AGENT if getattr(args, "guarded_pi", False) else DEFAULT_AGENT), "--model", args.model,
           "--base-url", endpoint, "--api-format", "openai_chat_completions",
           "--api-key-name", args.key_env,
           "--ak", f"max_turns={args.max_turns}",
           "--ak", f"prompt_template_path={PI_PROMPT}",
           "--n-concurrent", str(args.concurrency),
           "--n-attempts", str(args.attempts),
           "--n-tasks", str(args.tasks),
           "--job-name", args.job_name,
           "--jobs-dir", str(RESULTS_DIR / args.job_name / "harbor")]
    if args.pi_version:
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.pi_version):
            raise ValueError("Pi version must be a pinned X.Y.Z")
        cmd += ["--ak", f"version={args.pi_version}"]
    for name in args.include:
        if not re.fullmatch(r"[a-zA-Z0-9_.?*+-]+", name):
            raise ValueError("Invalid task filter")
        cmd += ["--include-task-name", "terminal-bench/" + name]
    if args.mode == "preflight":
        cmd.append("--dry-run")
    return cmd


def check_model(endpoint: str, model: str, key_env: str, *, timeout: float = 5.0) -> dict:
    """Confirm target exists, without logging credentials or responses."""
    url = normalize_endpoint(endpoint)
    headers = {}
    if os.getenv(key_env):
        headers["Authorization"] = f"Bearer {os.environ[key_env]}"
    request = Request(url + "/models", headers=headers)
    with urlopen(request, timeout=timeout) as response:
        listing = json.load(response)
    names = [str(item.get("id", "")) for item in listing.get("data", [])]
    slug = model.split("/", 1)[-1]
    if slug not in names:
        raise RuntimeError(f"Requested model not in endpoint catalog (found {len(names)} models)")
    return {"model_available": True, "catalog_size": len(names)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded Harbor / official TB4 / Pi runs")
    parser.add_argument("mode", choices=["preflight", "run"])
    parser.add_argument("--harbor", type=Path, default=DEFAULT_HARBOR)
    parser.add_argument("--model", required=True, help="Provider-prefixed name, e.g. openai/<id>")
    parser.add_argument("--endpoint", required=True, help="OpenAI-compatible base URL ending in /v1")
    parser.add_argument("--key-env", default="TB4_LOCAL_API_KEY", help="Variable name only; never a value")
    parser.add_argument("--tasks", type=int, default=1)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=32)
    parser.add_argument("--pi-version", default="0.85.1")
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--job-name", default="tb4-pi-canary")
    parser.add_argument("--min-free-gb", type=int, default=20)
    parser.add_argument("--disable-thinking-gateway", action="store_true",
                        help="Managed authenticated Docker-bridge gateway (no thinking)")
    parser.add_argument("--gateway-max-output-tokens", type=int, default=2048)
    parser.add_argument("--gateway-context-window", type=int, default=None,
                        help="Bound chat history to this server context window; enables managed gateway")
    parser.add_argument("--guarded-pi", action="store_true",
                        help="Use Pi tool-error repeat blocker (Harbor 0.25.0)")
    parser.add_argument("--probe-tools", action="store_true",
                        help="Run a tiny synthetic native-tool API capability probe")
    parser.add_argument("--require-tool-calls", action="store_true",
                        help="Abort if the synthetic native tool-call probe fails")
    parser.add_argument("--trace-events-out", type=Path, default=None,
                        help="Opt-in append-only private JSONL for Trace Viewer (absolute path)")
    parser.add_argument("--trace-host", default=None,
                        help="Physical inference host label for Trace Viewer metadata")
    parser.add_argument("--gateway-forward-auth", action="store_true",
                        help="Pass the configured key to upstream when upstream requires it")
    args = parser.parse_args(argv)
    if not args.model.startswith("openai/"):
        parser.error("For this OpenAI-compatible endpoint runner, use openai/<model-id>")
    if args.trace_events_out is not None:
        from tb4.trace_bridge import name
        if (not args.trace_events_out.is_absolute()
                or name(args.trace_host) != args.trace_host
                or args.trace_host in (None, "unknown")):
            parser.error("--trace-events-out requires an absolute path and --trace-host label")
    gateway = None
    gateway_thread = None
    gateway_telemetry = None
    job_environment = os.environ.copy()
    dest = None
    try:
        make_command(args)
        if not shutil.which(str(args.harbor)) and not args.harbor.exists():
            raise RuntimeError("Harbor executable unavailable")
        if not PI_PROMPT.is_file():
            raise RuntimeError("Pi prompt file missing")
        if not os.environ.get(args.key_env):
            raise RuntimeError(f"Set {args.key_env} (use a dummy string only for no-auth local endpoints)")
        model_check = check_model(args.endpoint, args.model, args.key_env)
        tool_check = None
        if args.probe_tools or args.require_tool_calls:
            from tb4.capabilities import probe_tool_call
            tool_check = probe_tool_call(args.endpoint, args.model, args.key_env,
                                         disable_thinking=args.disable_thinking_gateway)
            if args.require_tool_calls and not tool_check["native_tool_call"]:
                raise RuntimeError("Endpoint did not produce the required native tool call")
        disk = shutil.disk_usage(RESULTS_DIR.parent)
        free_gb = round(disk.free / (1024**3), 1)
        if args.mode == "run" and free_gb < args.min_free_gb:
            raise RuntimeError(f"Only {free_gb} GiB free; need {args.min_free_gb} GiB")
        if args.mode == "run":
            docker = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                    capture_output=True, text=True, timeout=10)
            if docker.returncode:
                raise RuntimeError("Docker daemon unavailable")
        dest = RESULTS_DIR / args.job_name
        if dest.exists() and args.mode == "run":
            raise RuntimeError("Run directory already exists; use a unique job name")
        if (args.disable_thinking_gateway or args.gateway_context_window) and args.mode == "run":
            from tb4.gateway import make_handler
            from threading import Lock
            gateway_telemetry = {"lock": Lock(), "requests": 0, "tool_outputs_compacted": 0,
                                 "old_turns_dropped": 0, "max_estimated_prompt_tokens": 0}
            if args.gateway_context_window is not None and not (2048 <= args.gateway_context_window <= 262144):
                raise ValueError("gateway-context-window must be 2048..262144")
            if not (64 <= args.gateway_max_output_tokens <= 32768):
                raise ValueError("gateway-max-output-tokens must be 64-32768")
            gateway_token = secrets.token_urlsafe(32)
            job_environment[args.key_env] = gateway_token
            gateway = ThreadingHTTPServer(("172.17.0.1", 0), make_handler(
                args.endpoint, gateway_token, disable_thinking=args.disable_thinking_gateway,
                max_output_tokens=args.gateway_max_output_tokens,
                forward_auth=args.gateway_forward_auth,
                upstream_key=(os.environ[args.key_env] if args.gateway_forward_auth else None),
                context_window=args.gateway_context_window, telemetry=gateway_telemetry))
            gateway_thread = Thread(target=gateway.serve_forever, daemon=True)
            gateway_thread.start()
        endpoint_for_agent = (f"http://172.17.0.1:{gateway.server_port}/v1"
                              if gateway is not None else args.endpoint)
        command = make_command(args, endpoint_override=endpoint_for_agent)
        dest.mkdir(parents=True, exist_ok=True)
        manifest = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "dataset": DATASET, "agent": (GUARDED_AGENT if args.guarded_pi else DEFAULT_AGENT),
                    "model": args.model,
                    "endpoint": normalize_endpoint(args.endpoint), "mode": args.mode,
                    "agent_endpoint": endpoint_for_agent,
                    "gateway_disable_thinking": bool(gateway and args.disable_thinking_gateway),
                    "gateway_context_window": args.gateway_context_window,
                    "gateway_max_output_tokens": (args.gateway_max_output_tokens if gateway else None),
                    "gateway_forward_auth": (args.gateway_forward_auth if gateway else None),
                    "tasks_limit": args.tasks, "attempts": args.attempts,
                    "concurrency": args.concurrency, "max_turns": args.max_turns,
                    "pi_version": args.pi_version, "free_gib": free_gb,
                    "trace_metadata_export_enabled": bool(args.trace_events_out),
                    "trace_host": args.trace_host if args.trace_events_out else None,
                    "prompt_sha256": hashlib.sha256(PI_PROMPT.read_bytes()).hexdigest(),
                    "model_check": model_check, "synthetic_tool_probe": tool_check,
                    "command": ["<KEY_ENV_NAME>" if x == args.key_env else x for x in command]}
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({"ready": True, "mode": args.mode, "manifest": str(dest / 'manifest.json'),
                          "model": args.model, "disk_free_gib": free_gb}), flush=True)
        with (dest / f"{args.mode}.log").open("w") as logfile:
            if args.mode == "run" and args.trace_events_out is not None:
                from tb4.trace_bridge import Reader
                reader = Reader(dest, args.trace_host)
                with subprocess.Popen(command, stdout=logfile, stderr=subprocess.STDOUT,
                                      env=job_environment) as process:
                    try:
                        while process.poll() is None:
                            reader.export_to(args.trace_events_out)
                            time.sleep(2)
                    finally:
                        reader.export_to(args.trace_events_out)
                    exit_code = process.wait()
            else:
                exit_code = subprocess.run(command, stdout=logfile,
                                           stderr=subprocess.STDOUT,
                                           env=job_environment, check=False).returncode
        print(json.dumps({"harbor_exit_code": exit_code,
                          "log": str(dest / f'{args.mode}.log')}), flush=True)
        return exit_code
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if gateway is not None:
            if gateway_telemetry is not None and dest is not None and dest.exists():
                stats = {k:v for k,v in gateway_telemetry.items() if k != "lock"}
                (dest / "gateway-telemetry.json").write_text(json.dumps(stats, indent=2)+"\n")
            gateway.shutdown()
            gateway.server_close()
            if gateway_thread is not None:
                gateway_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
