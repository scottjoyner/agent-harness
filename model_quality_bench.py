#!/usr/bin/env python3
"""Non-destructive quality benchmark for model-selection evidence.

This runner never executes model-produced tools. Tool tasks score only the
model's proposed OpenAI-compatible tool call. Long-context tasks inject
deterministic filler plus a known needle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "benchmarks/model_selection/tasks.json"


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_manifest(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "model-quality-suite-v1":
        raise ValueError("unsupported model quality suite schema")
    ids: set[str] = set()
    for task in payload.get("tasks", []):
        if not isinstance(task, dict):
            raise ValueError("tasks must be objects")
        task_id = str(task.get("id") or "")
        family = str(task.get("family") or "")
        if not task_id or task_id in ids:
            raise ValueError("task IDs must be non-empty and unique")
        ids.add(task_id)
        if not family or not task.get("prompt") or not task.get("validator"):
            raise ValueError(f"incomplete task {task_id}")
        validator = task["validator"]
        if validator.get("type") not in {
            "json_exact",
            "tool_call_exact",
            "exact_text",
        }:
            raise ValueError(f"unsupported validator in {task_id}")
        if validator.get("type") == "tool_call_exact" and not task.get("tools"):
            raise ValueError(f"tool task {task_id} requires tools")
        if family == "long_context_retrieval" and not task.get("long_context"):
            raise ValueError(f"long-context task {task_id} requires needle data")

    generator_types = {
        "decision_policy_grid",
        "structured_edit_grid",
        "repo_fix_grid",
        "tool_choice_grid",
        "long_context_grid",
        "summary_grid",
    }
    generator_ids: set[str] = set()
    for spec in payload.get("generators", []):
        if not isinstance(spec, dict):
            raise ValueError("generators must be objects")
        generator_id = str(spec.get("id") or "")
        if not generator_id or generator_id in generator_ids:
            raise ValueError("generator IDs must be non-empty and unique")
        generator_ids.add(generator_id)
        if spec.get("type") not in generator_types:
            raise ValueError(f"unsupported generator {generator_id}")
        if int(spec.get("generated_case_count") or 0) <= 0:
            raise ValueError(f"generator {generator_id} requires a positive case count")


def endpoint_url(endpoint: str) -> str:
    value = endpoint.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return value + "/chat/completions"
    return value + "/v1/chat/completions"


def make_long_context(task: dict[str, Any], context_tokens: int) -> str:
    if context_tokens <= 0:
        raise ValueError("--context-tokens must be positive for long-context tasks")
    needle = str(task["long_context"]["needle"])
    target_chars = max(4096, context_tokens * 4)
    filler = (
        "Archive record: routine telemetry was verified and contains no decision "
        "for the current question. Preserve the current instruction.\n"
    )
    left_target = int(target_chars * 0.67)
    left = (filler * (left_target // len(filler) + 1))[:left_target]
    right_target = max(0, target_chars - len(left) - len(needle))
    right = (filler * (right_target // len(filler) + 1))[:right_target]
    return (
        task["prompt"]
        + "\n\nBEGIN CONTEXT\n"
        + left
        + "\n"
        + needle
        + "\n"
        + right
        + "\nEND CONTEXT"
    )


def request_payload(
    task: dict[str, Any],
    model: str,
    reasoning_mode: str,
    context_tokens: int,
) -> dict[str, Any]:
    prompt = str(task["prompt"])
    if task["family"] == "long_context_retrieval":
        prompt = make_long_context(task, context_tokens)

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": str(task.get("system") or "")},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "seed": 17,
        "max_tokens": 512,
        "stream": False,
    }
    if task.get("tools"):
        payload["tools"] = task["tools"]
        payload["tool_choice"] = "auto"

    if reasoning_mode not in {"", "default"}:
        if reasoning_mode == "none":
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        elif reasoning_mode in {"reasoning", "think"}:
            payload["chat_template_kwargs"] = {"enable_thinking": True}
        else:
            payload["reasoning_effort"] = reasoning_mode
    return payload


def call_model(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"content-type": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
        return {
            "ok": True,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "body": body,
        }
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {
            "ok": False,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": repr(exc),
        }


def message_from_response(response: dict[str, Any]) -> dict[str, Any]:
    try:
        return response["body"]["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return {}


def parse_tool_arguments(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def score_task(
    task: dict[str, Any],
    response: dict[str, Any],
) -> tuple[bool, str]:
    if not response.get("ok"):
        return False, "request_error"

    message = message_from_response(response)
    validator = task["validator"]
    kind = validator["type"]

    if kind == "json_exact":
        content = message.get("content")
        if not isinstance(content, str):
            return False, "missing_text_content"
        try:
            actual = json.loads(content)
        except json.JSONDecodeError:
            return False, "invalid_json"
        return (
            (actual == validator["expected"], "exact_json_match" if actual == validator["expected"] else "json_mismatch")
        )

    if kind == "exact_text":
        actual = str(message.get("content") or "").strip()
        expected = str(validator.get("expected") or "").strip()
        return (actual == expected, "exact_text_match" if actual == expected else "text_mismatch")

    if kind == "tool_call_exact":
        calls = message.get("tool_calls") or []
        if len(calls) != 1:
            return False, "expected_one_tool_call"
        function = calls[0].get("function") or {}
        if function.get("name") != validator["name"]:
            return False, "wrong_tool"
        arguments = parse_tool_arguments(function.get("arguments"))
        if arguments != validator["arguments"]:
            return False, "wrong_tool_arguments"
        return True, "exact_tool_call_match"

    return False, "unsupported_validator"



def _tool_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read one file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_tests",
                "description": "Run a named test target",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
            },
        },
    ]


def _decision_grid(count: int) -> list[dict[str, Any]]:
    tasks = []
    labels = (
        "tests_passed",
        "scope_clean",
        "evidence_complete",
        "operator_blocker",
        "artifact_available",
        "authority_preserved",
    )
    for index in range(count):
        state = {
            label: bool(index & (1 << bit))
            for bit, label in enumerate(labels)
        }
        if state["operator_blocker"]:
            decision = "delegate"
        elif not state["evidence_complete"] or not state["artifact_available"]:
            decision = "abstain"
        elif (
            not state["tests_passed"]
            or not state["scope_clean"]
            or not state["authority_preserved"]
        ):
            decision = "retry"
        else:
            decision = "accept"
        facts = ", ".join(
            f"{key}={str(value).lower()}" for key, value in state.items()
        )
        tasks.append({
            "id": f"G-DJ-{index:03d}",
            "family": "decision_judge",
            "system": "Return only JSON with one key named decision.",
            "prompt": (
                "Apply this precedence: operator-only blocker -> delegate; "
                "missing evidence or unavailable exact artifact -> abstain; "
                "failed tests, scope drift, or authority drift -> retry; "
                "otherwise accept. State: " + facts
            ),
            "validator": {
                "type": "json_exact",
                "expected": {"decision": decision},
            },
        })
    return tasks


def _structured_grid(count: int) -> list[dict[str, Any]]:
    fields = ("timeout", "retries", "context", "batch")
    values = (5, 7, 11, 13, 17, 19)
    base = {"timeout": 30, "retries": 3, "context": 8192, "batch": 1}
    tasks = []
    for index in range(count):
        field = fields[index % len(fields)]
        value = values[(index // len(fields)) % len(values)]
        expected = dict(base)
        expected[field] = value
        tasks.append({
            "id": f"G-SO-{index:03d}",
            "family": "structured_output",
            "system": "Return only valid JSON. Preserve every unmentioned value exactly.",
            "prompt": (
                f"Change {field} to {value} in "
                + json.dumps(base, separators=(",", ":"))
            ),
            "validator": {"type": "json_exact", "expected": expected},
        })
    return tasks


def _repo_grid(count: int) -> list[dict[str, Any]]:
    cases = [
        ("src/auth.py", "identity comparison uses is", "replace is with =="),
        ("src/cache.py", "clear returns without clearing storage", "clear the backing dict"),
        ("src/config.py", "missing environment default crashes startup", "add the documented default before conversion"),
        ("src/parser.py", "empty input indexes element zero", "handle empty input before indexing"),
        ("src/client.py", "HTTP response body is read twice", "read and parse the response once"),
        ("src/router.py", "fallback silently changes selected model", "fail closed when the exact model is unavailable"),
        ("src/state.py", "mutable default list is shared", "use a per-instance default factory"),
        ("src/io.py", "file handle is not closed on error", "use a context manager"),
        ("src/retry.py", "retry loop attempts one extra time", "fix the loop bound"),
        ("src/schema.py", "unknown fields are silently accepted", "forbid unknown fields"),
        ("src/clock.py", "naive timestamps are compared with UTC timestamps", "normalize timestamps to timezone-aware UTC"),
        ("src/hash.py", "artifact identity uses filename only", "bind identity to a cryptographic fingerprint"),
    ]
    tasks = []
    for index in range(count):
        file_name, bug, change = cases[index % len(cases)]
        tasks.append({
            "id": f"G-RW-{index:03d}",
            "family": "repo_work",
            "system": "Return only JSON with file and change.",
            "prompt": f"The defect is: {bug}. It is in {file_name}. Give the minimal semantic fix.",
            "validator": {
                "type": "json_exact",
                "expected": {"file": file_name, "change": change},
            },
        })
    return tasks


def _tool_grid(count: int) -> list[dict[str, Any]]:
    read_paths = (
        "README.md",
        "src/main.py",
        "src/router.py",
        "src/config.py",
        "pyproject.toml",
        "tests/test_router.py",
    )
    test_targets = (
        "tests/test_router.py",
        "tests/test_parser.py",
        "tests/test_config.py",
        "tests/test_schema.py",
        "tests/test_client.py",
        "tests/test_state.py",
    )
    tasks = []
    for index in range(count):
        if index % 2 == 0:
            path = read_paths[(index // 2) % len(read_paths)]
            prompt = f"Inspect {path} before deciding what to change."
            expected_name = "read_file"
            expected_args = {"path": path}
        else:
            target = test_targets[(index // 2) % len(test_targets)]
            prompt = f"The implementation is complete. Validate only {target}."
            expected_name = "run_tests"
            expected_args = {"target": target}
        tasks.append({
            "id": f"G-TA-{index:03d}",
            "family": "terminal_agent",
            "system": "Choose exactly one provided tool call. Do not execute or describe it.",
            "prompt": prompt,
            "tools": _tool_schema(),
            "validator": {
                "type": "tool_call_exact",
                "name": expected_name,
                "arguments": expected_args,
            },
        })
    return tasks


def _long_context_grid(count: int) -> list[dict[str, Any]]:
    tasks = []
    for index in range(count):
        code = f"CTX-{index:02d}-{(index * 7919 + 104729) % 100000:05d}"
        tasks.append({
            "id": f"G-LC-{index:03d}",
            "family": "long_context_retrieval",
            "system": "Return only JSON with the key answer.",
            "prompt": "Find the unique evidence key in the supplied context.",
            "long_context": {
                "needle": f"The evidence key is {code}.",
                "expected": {"answer": code},
            },
            "validator": {
                "type": "json_exact",
                "expected": {"answer": code},
            },
        })
    return tasks


def _summary_grid(count: int) -> list[dict[str, Any]]:
    owners = ("Mina", "Ravi", "Lena", "Omar", "Iris")
    blockers = (
        "missing signing proof",
        "unverified artifact identity",
        "failing regression test",
        "missing operator approval",
    )
    next_steps = (
        "capture signed-device validation",
        "record the exact artifact fingerprint",
        "fix the regression and rerun the test",
        "request the required operator approval",
    )
    tasks = []
    for index in range(count):
        owner = owners[index % len(owners)]
        slot = index % len(blockers)
        blocker = blockers[slot]
        next_step = next_steps[slot]
        project = f"Project-{index:02d}"
        tasks.append({
            "id": f"G-SM-{index:03d}",
            "family": "summarization",
            "system": "Return only JSON with keys project, owner, blocker, next.",
            "prompt": (
                f"{project} is owned by {owner}. The only current blocker is "
                f"{blocker}. The next action is to {next_step}. An older note "
                "mentions unrelated infrastructure work; ignore it."
            ),
            "validator": {
                "type": "json_exact",
                "expected": {
                    "project": project,
                    "owner": owner,
                    "blocker": blocker,
                    "next": next_step,
                },
            },
        })
    return tasks


def generated_tasks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    builders = {
        "decision_policy_grid": _decision_grid,
        "structured_edit_grid": _structured_grid,
        "repo_fix_grid": _repo_grid,
        "tool_choice_grid": _tool_grid,
        "long_context_grid": _long_context_grid,
        "summary_grid": _summary_grid,
    }
    tasks: list[dict[str, Any]] = []
    for spec in manifest.get("generators", []):
        builder = builders[str(spec["type"])]
        generated = builder(int(spec["generated_case_count"]))
        family = str(spec["family"])
        if any(task.get("family") != family for task in generated):
            raise ValueError(f"generator {spec['id']} produced the wrong family")
        tasks.extend(generated)
    return tasks


def select_tasks(
    manifest: dict[str, Any],
    families: list[str],
    cases: list[str],
) -> list[dict[str, Any]]:
    selected = list(manifest.get("tasks", [])) + generated_tasks(manifest)
    if families:
        wanted = set(families)
        selected = [task for task in selected if task.get("family") in wanted]
    if cases:
        wanted_cases = set(cases)
        selected = [task for task in selected if task.get("id") in wanted_cases]
    return selected


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_json(args.manifest)
    validate_manifest(manifest)
    tasks = select_tasks(manifest, args.family, args.case)

    if args.validate_only:
        return {
            "schema_version": "model-quality-validation-v1",
            "suite_revision": manifest["suite_revision"],
            "manifest_sha256": sha256_json(manifest),
            "runner_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
            "task_count": len(tasks),
            "families": sorted({task["family"] for task in tasks}),
        }

    if not tasks:
        raise ValueError("no tasks selected")
    if not args.endpoint or not args.model:
        raise ValueError("--endpoint and --model are required for execution")
    if not args.artifact or not args.artifact_sha256:
        raise ValueError("--artifact and --artifact-sha256 are required")
    if len(args.artifact_sha256) != 64:
        raise ValueError("--artifact-sha256 must be a SHA-256 hex digest")
    int(args.artifact_sha256, 16)
    if not args.runtime_id:
        raise ValueError("--runtime-id is required")
    if args.reasoning_mode not in {"default", "none"} and not args.reasoning_policy_sha256:
        raise ValueError(
            "--reasoning-policy-sha256 is required for an explicit reasoning mode"
        )
    if args.reasoning_policy_sha256:
        if len(args.reasoning_policy_sha256) != 64:
            raise ValueError("--reasoning-policy-sha256 must be SHA-256")
        int(args.reasoning_policy_sha256, 16)

    url = endpoint_url(args.endpoint)
    results: list[dict[str, Any]] = []
    for task in tasks:
        payload = request_payload(
            task,
            args.model,
            args.reasoning_mode,
            args.context_tokens,
        )
        response = call_model(url, payload, args.timeout)
        success, outcome = score_task(task, response)
        body = response.get("body") or {}
        message = message_from_response(response)
        results.append({
            "case_id": task["id"],
            "task_family": task["family"],
            "success": success,
            "outcome": outcome,
            "elapsed_ms": response["elapsed_ms"],
            "usage": body.get("usage"),
            "finish_reason": (
                (body.get("choices") or [{}])[0].get("finish_reason")
                if isinstance(body.get("choices"), list)
                else None
            ),
            "content_preview": str(message.get("content") or "")[:1000],
            "tool_calls": message.get("tool_calls") or [],
            "error": response.get("error"),
        })

    passed = sum(row["success"] for row in results)
    return {
        "schema_version": "model-quality-benchmark-result-v1",
        "suite_revision": manifest["suite_revision"],
        "manifest_sha256": sha256_json(manifest),
        "runner_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "observed_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "model": args.model,
        "artifact": args.artifact,
        "artifact_sha256": args.artifact_sha256,
        "runtime_id": args.runtime_id,
        "reasoning_mode": args.reasoning_mode,
        "reasoning_policy_sha256": args.reasoning_policy_sha256,
        "context_tokens": args.context_tokens,
        "task_count": len(results),
        "unique_case_count": len({row["case_id"] for row in results}),
        "passed": passed,
        "success_rate": passed / len(results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--endpoint")
    parser.add_argument("--model")
    parser.add_argument("--artifact")
    parser.add_argument("--artifact-sha256")
    parser.add_argument("--runtime-id")
    parser.add_argument(
        "--reasoning-mode",
        default="default",
        help="default, none, reasoning/think, or provider/runtime effort such as low/high/xhigh",
    )
    parser.add_argument("--reasoning-policy-sha256")
    parser.add_argument("--context-tokens", type=int, default=0)
    parser.add_argument("--family", action="append", default=[])
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()

    result = run(args)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
