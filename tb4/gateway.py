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



def _estimate_prompt_tokens(payload: dict) -> int:
    """Conservative character estimate, not a model-specific tokenizer."""
    pieces = [payload.get("messages", []), payload.get("tools", []), payload.get("tool_choice", None)]
    return int(sum(len(json.dumps(x, ensure_ascii=False)) for x in pieces) / 2.5) + 24 * len(payload.get("messages", []))


def compact_chat_history(payload: dict, *, context_window: int,
                         output_budget: int, safety_margin: int = 640) -> dict:
    """Bound old history, never trim system, user instructions, or tool definitions.

    Shorten old tool outputs first; remove entire old assistant+tool groups only
    if necessary. Keep latest six messages intact. Fail closed if immutable task
    instructions and recent state cannot fit; never truncate the task prompt.
    """
    if context_window < 2048 or output_budget < 64:
        raise ValueError("Invalid context or output budget")
    available = context_window - output_budget - safety_margin
    if available < 1000:
        raise ValueError("Not enough prompt context after output reservation")
    messages = payload.get("messages")
    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
        raise ValueError("messages must be a list of objects")
    # Do not modify caller's messages in-place.
    messages = json.loads(json.dumps(messages))
    payload["messages"] = messages
    if _estimate_prompt_tokens(payload) <= available:
        return {"old_tool_outputs_compacted": 0, "old_turns_dropped": 0, "estimated_tokens": _estimate_prompt_tokens(payload)}
    keep_tail = 2
    # Do not touch any system/developer or user instruction message, even if old.
    protected = {i for i, m in enumerate(messages) if m.get("role") in {"system", "developer", "user"}}
    changed, dropped = 0, 0
    for i, msg in enumerate(messages):
        if _estimate_prompt_tokens(payload) <= available:
            break
        if i >= len(messages) - keep_tail or i in protected or msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if isinstance(content, str) and len(content) > 320:
            msg["content"] = content[:160] + "\n[Older tool output omitted by bounded-context gateway. Re-run the tool if necessary.]\n" + content[-80:]
            changed += 1
    # Recent tool results can themselves be larger than the backend's entire
    # window. Shorten them only as a last resort, retaining the beginning and
    # end so the model can choose to re-read targeted sections.
    for i, msg in enumerate(messages):
        if _estimate_prompt_tokens(payload) <= available:
            break
        if msg.get("role") != "tool" or not isinstance(msg.get("content"), str):
            continue
        content = msg["content"]
        if len(content) > 500:
            msg["content"] = content[:300] + "\n[Tool output abbreviated for context. Request narrow sections as needed.]\n" + content[-100:]
            changed += 1
    # If still oversized, drop complete prior assistant/tool groups; never a
    # dangling result without its tool_call and never the latest six messages.
    while _estimate_prompt_tokens(payload) > available:
        start = next((i for i, msg in enumerate(messages[:max(0, len(messages)-keep_tail)])
                      if msg.get("role") == "assistant"), None)
        if start is None:
            break
        end = start + 1
        while end < len(messages) and messages[end].get("role") == "tool":
            end += 1
        if end > len(messages) - keep_tail:
            break
        del messages[start:end]
        dropped += 1
    estimate = _estimate_prompt_tokens(payload)
    if estimate > available:
        raise ValueError("Immutable instructions and recent turns exceed configured context; aborting instead of truncating task")
    return {"old_tool_outputs_compacted": changed, "old_turns_dropped": dropped, "estimated_tokens": estimate}


def tune_body(body: bytes, *, disable_thinking: bool, max_output_tokens: int | None,
              context_window: int | None = None, telemetry: dict | None = None) -> bytes:
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
    if context_window is not None:
        stats = compact_chat_history(payload, context_window=context_window,
                                     output_budget=max_output_tokens or 1024)
        if telemetry is not None:
            with telemetry["lock"]:
                telemetry["requests"] += 1
                telemetry["tool_outputs_compacted"] += stats["old_tool_outputs_compacted"]
                telemetry["old_turns_dropped"] += stats["old_turns_dropped"]
                telemetry["max_estimated_prompt_tokens"] = max(telemetry["max_estimated_prompt_tokens"], stats["estimated_tokens"])
    return json.dumps(payload, separators=(",", ":")).encode()


def make_handler(upstream: str, key: str, *, disable_thinking: bool,
                 max_output_tokens: int | None = None, forward_auth: bool = False,
                 context_window: int | None = None, telemetry: dict | None = None,
                 upstream_key: str | None = None):
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
                                             max_output_tokens=max_output_tokens,
                                             context_window=context_window, telemetry=telemetry)
                except (ValueError, json.JSONDecodeError):
                    self.send_error(400, "Invalid JSON payload")
                    return
            headers = {"Content-Type": "application/json"}
            # Keep the random per-trial gateway token separate from upstream credentials.
            if forward_auth:
                headers["Authorization"] = "Bearer " + (upstream_key if upstream_key is not None else key)
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
    parser.add_argument("--context-window", type=int, default=None,
                        help="Optional conservative chat history budgeting in tokens")
    args = parser.parse_args()
    if args.max_output_tokens < 64:
        parser.error("Minimum max output is 64")
    key = os.environ.get(args.key_env)
    handler = make_handler(args.upstream, key or "", disable_thinking=args.disable_thinking,
                           max_output_tokens=args.max_output_tokens, forward_auth=args.forward_auth,
                           context_window=args.context_window)
    with ThreadingHTTPServer((args.bind, args.port), handler) as server:
        print(json.dumps({"listening": f"http://{args.bind}:{args.port}/v1",
                          "upstream": args.upstream,
                          "thinking_disabled": args.disable_thinking}), flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
