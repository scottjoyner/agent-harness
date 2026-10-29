"""Tiny synthetic native-tool-call probe; never uses benchmark task content."""
from __future__ import annotations
import json
import os
import time
from urllib.request import Request, urlopen


def probe_tool_call(endpoint: str, model: str, key_env: str, *,
                    disable_thinking: bool = False, timeout: int = 25) -> dict:
    """Return observed tool-API compatibility without claiming benchmark success."""
    from tb4.runner import normalize_endpoint
    url = normalize_endpoint(endpoint)
    if not os.getenv(key_env):
        raise RuntimeError(f"Missing credential environment variable: {key_env}")
    api_model = model.split("/", 1)[-1]
    body = {"model": api_model,
            "messages": [
                {"role": "system", "content": "You must use the supplied tool when requested."},
                {"role": "user", "content": "Call tb4_probe with label ready. Do not write a text answer."},
            ],
            "tools": [{"type": "function", "function": {
                "name": "tb4_probe", "description": "Return the requested diagnostic label.",
                "parameters": {"type": "object", "properties": {
                    "label": {"type": "string"}}, "required": ["label"]}}}],
            "tool_choice": "required", "max_tokens": 256, "temperature": 0,
            "stream": False}
    if disable_thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    request = Request(url + "/chat/completions", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + os.environ[key_env]})
    start = time.monotonic()
    with urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    choices = data.get("choices") or []
    choice = choices[0] if choices else {}
    msg = choice.get("message") or {}
    calls = msg.get("tool_calls") or []
    valid_call = False
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            continue
        function = call.get("function") or {}
        if function.get("name") != "tb4_probe":
            continue
        try:
            arguments = function.get("arguments") or "{}"
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("label") == "ready":
            valid_call = True

    return {"native_tool_call": valid_call, "calls_returned": len(calls),
            "response_finish_reason": choice.get("finish_reason", data.get("finish_reason")),
            "model": api_model, "disable_thinking": disable_thinking,
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "input_tokens": (data.get("usage") or {}).get("prompt_tokens"),
            "output_tokens": (data.get("usage") or {}).get("completion_tokens"),
            "content_length": len(msg.get("content") or ""),
            "reasoning_length": len(msg.get("reasoning_content") or "")}
