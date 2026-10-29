"""Private, streaming OpenAI-chat gateway for tool-call-friendly fleet inference.

No tool invocation or grader modifications happen here; this only configures
backend inference parameters and forwards model responses transparently.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_REQUEST_BYTES = 2 * 1024 * 1024


def tune_body(body: bytes, *, disable_thinking: bool, max_output_tokens: int | None) -> bytes:
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON request object")
    if disable_thinking:
        options = payload.get("chat_template_kwargs")
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise ValueError("chat_template_kwargs must be an object")
        payload["chat_template_kwargs"] = {**options, "enable_thinking": False}
    if max_output_tokens is not None:
        for name in ("max_tokens", "max_completion_tokens"):
            current = payload.get(name)
            if current is not None and isinstance(current, int) and current > max_output_tokens:
                payload[name] = max_output_tokens
    return json.dumps(payload, separators=(",", ":")).encode()


def make_handler(upstream: str, key: str, *, disable_thinking: bool,
                 max_output_tokens: int | None = None, forward_auth: bool = False):
    """Bind upstream and auth once; requests cannot choose arbitrary URLs."""
    upstream = upstream.rstrip("/")
    if not upstream.endswith("/v1"):
        raise ValueError("Upstream must end with /v1")
    parsed = urlsplit(upstream)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError("Invalid upstream")
    if not key:
        raise ValueError("Gateway requires authentication")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # close stream when upstream finishes
        def log_message(self, *_args):
            return  # no URLs, headers, prompts, credentials in logs

        def _handle(self):
            if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + key):
                self.send_error(401, "Unauthorized")
                return
            path = self.path
            if path not in {"/v1/models", "/v1/chat/completions"}:
                self.send_error(404, "Not found")
                return
            if self.command == "GET" and path != "/v1/models":
                self.send_error(405)
                return
            if self.command == "POST" and path != "/v1/chat/completions":
                self.send_error(405)
                return
            request_body = None
            if self.command == "POST":
                try:
                    size = int(self.headers.get("Content-Length", "-1"))
                    if size < 0 or size > MAX_REQUEST_BYTES:
                        self.send_error(413, "Request too large or missing length")
                        return
                    request_body = tune_body(self.rfile.read(size),
                                             disable_thinking=disable_thinking,
                                             max_output_tokens=max_output_tokens)
                except (ValueError, json.JSONDecodeError):
                    self.send_error(400, "Invalid JSON payload")
                    return
            headers = {"Content-Type": "application/json"}
            # Only relay the configured gateway credential when explicitly enabled.
            if forward_auth:
                headers["Authorization"] = "Bearer " + key
            upstream_request = Request(upstream + path[len('/v1'):],
                                       data=request_body, headers=headers,
                                       method=self.command)
            try:
                with urlopen(upstream_request, timeout=300) as response:
                    self.send_response(response.status)
                    self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                    self.end_headers()
                    while chunk := response.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except HTTPError as exc:
                self.send_response(exc.code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(exc.read()[:65536])
            except (URLError, TimeoutError, BrokenPipeError, ConnectionResetError):
                # No retry here: silently replaying a generation can duplicate effects.
                try:
                    self.send_error(502, "Upstream unreachable")
                except OSError:
                    pass
        def do_GET(self): self._handle()
        def do_POST(self): self._handle()
    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Authenticated, narrow fleet chat inference gateway")
    parser.add_argument("--bind", default="172.17.0.1")
    parser.add_argument("--port", type=int, default=19221)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--key-env", default="TB4_LOCAL_API_KEY")
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument("--forward-auth", action="store_true")
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    args = parser.parse_args()
    if args.max_output_tokens < 64:
        parser.error("Minimum max output is 64")
    key = os.environ.get(args.key_env)
    handler = make_handler(args.upstream, key or "", disable_thinking=args.disable_thinking,
                           max_output_tokens=args.max_output_tokens, forward_auth=args.forward_auth)
    with ThreadingHTTPServer((args.bind, args.port), handler) as server:
        print(json.dumps({"listening": f"http://{args.bind}:{args.port}/v1",
                          "upstream": args.upstream,
                          "thinking_disabled": args.disable_thinking}), flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
