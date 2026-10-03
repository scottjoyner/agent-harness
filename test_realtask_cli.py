"""End-to-end CLI tests, including against a loopback OpenAI-compatible endpoint.

The loopback server is a stand-in for "somebody already started a model
runtime". It exists so the adapter, the streaming parse, the TTFT measurement,
the run-directory layout and the comparison artifact are all exercised end to
end without touching a real node. The harness never starts a model runtime; it
only POSTs to one.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from test_realtask_support import (
    AUTO_INGEST_BUG_FIX,
    AUTO_INGEST_CONTRACT,
    REPO_ROOT,
    REFERENCE_REPAIR,
    HarnessTestCase,
)

ENTRYPOINT = REPO_ROOT / "realtime_bench.py"


class LoopbackModelHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible /v1/chat/completions with SSE streaming."""

    protocol_version = "HTTP/1.1"
    by_role: dict = {}
    fallback: str = ""
    requests: list = []

    def log_message(self, *_args):  # keep test output clean
        return

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(payload)
        reply = type(self).by_role.get(_detect_role(payload), type(self).fallback)

        if not payload.get("stream"):
            body = json.dumps(
                {
                    "choices": [
                        {"message": {"content": reply}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 4242, "completion_tokens": 77, "total_tokens": 4319},
                    "timings": {"predicted_per_second": 11.5, "prompt_per_second": 210.0},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        chunks = [
            {"choices": [{"delta": {"content": part}, "finish_reason": None}]}
            for part in _slice(reply, 5)
        ]
        chunks.append(
            {
                "choices": [{"delta": {"content": ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 4242, "completion_tokens": 77, "total_tokens": 4319},
                "timings": {"predicted_per_second": 11.5, "prompt_per_second": 210.0},
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in chunks:
            self._write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self._write(b"data: [DONE]\n\n")
        self._write(b"")

    def _write(self, payload: bytes) -> None:
        self.wfile.write("{:X}\r\n".format(len(payload)).encode() + payload + b"\r\n")
        self.wfile.flush()


def _slice(text: str, size: int):
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def patch_payload(patch: str = REFERENCE_REPAIR) -> str:
    return json.dumps(
        {
            "patch": patch,
            "tests": ["driver liveness"],
            "assumptions": ["finally close retained"],
            "confidence": 0.9,
        }
    )


def scout_payload() -> str:
    return json.dumps(
        {
            "root_cause": "auto_ingest/shorts/cli.py closes the driver before plan_shorts runs",
            "relevant_files": ["auto_ingest/shorts/cli.py"],
            "plan": ["move the call inside try"],
            "risks": ["driver leak"],
            "confidence": 0.8,
        }
    )


def analysis_payload() -> str:
    """A contract-reasoning answer that satisfies the fixture's answer check."""
    return json.dumps(
        {
            "root_cause": (
                "auto_ingest/shorts/cli.py closes the driver in a finally block, then calls "
                "planner.plan_shorts with the already-closed driver. The content mining is "
                "wrapped in a blanket except, so the command still exits 0 and silently "
                "falls back to templated text. A correct repair must plan while the driver "
                "is still live and keep the finally close so discussion-mode cleanup stays "
                "correct."
            ),
            "relevant_files": ["auto_ingest/shorts/cli.py"],
            "plan": ["move the planning call inside the try block", "retain the finally close"],
            "risks": ["driver leak on the exception path"],
            "confidence": 0.85,
        }
    )


def review_payload() -> str:
    return json.dumps(
        {
            "defects": [
                {"severity": "low", "location": "cli.py:71", "description": "no lifecycle test"}
            ],
            "missing_coverage": ["driver liveness regression test"],
            "contract_violations": [],
            "verdict": "accept",
            "confidence": 0.85,
        }
    )


def _detect_role(payload) -> str:
    """Which role contract is this request asking for?"""
    blob = "\n".join(
        str(message.get("content", "")) for message in payload.get("messages", [])
    )
    for role in ("REVIEWER", "IMPLEMENTER", "SINGLE", "SCOUT"):
        if "ROLE: " + role in blob:
            return role.lower()
    return "unknown"


class LoopbackEndpoint:
    """Stands in for a model runtime somebody else already started."""

    def __init__(self, by_role, fallback=""):
        handler = type(
            "BoundHandler", (LoopbackModelHandler,),
            {"by_role": dict(by_role), "fallback": fallback, "requests": []},
        )
        self.handler = handler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return "http://{}:{}/v1".format(host, port)

    @property
    def requests(self):
        return self.handler.requests

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class CliTestCase(HarnessTestCase):
    def cli(self, *args: str, expect: int = 0):
        completed = subprocess.run(
            [sys.executable, str(ENTRYPOINT), *args],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(
            completed.returncode, expect,
            "stdout:\n{}\nstderr:\n{}".format(completed.stdout, completed.stderr),
        )
        return completed

    def cli_json(self, *args: str):
        return json.loads(self.cli(*args).stdout)


class ValidateAndListTests(CliTestCase):
    def test_validate_all_fixtures_without_an_endpoint(self):
        payload = self.cli_json("validate", "--json")
        self.assertEqual(payload["failures"], 0)
        self.assertEqual(payload["count"], 5)
        for row in payload["tasks"]:
            self.assertTrue(row["binding_ok"], row)

    def test_validate_is_repeatable(self):
        first = self.cli_json("validate", "--json")
        second = self.cli_json("validate", "--json")
        self.assertEqual(
            [t["fixture_sha256"] for t in first["tasks"]],
            [t["fixture_sha256"] for t in second["tasks"]],
        )

    def test_list_reports_families(self):
        rows = self.cli_json("list", "--json")
        families = {row["task_family"] for row in rows}
        for family in ("bug_fix", "test_generation", "code_review", "small_refactor",
                       "contract_reasoning"):
            self.assertIn(family, families)

    def test_unknown_task_is_rejected_with_the_available_list(self):
        completed = self.cli("run", "--task", "no_such_task", expect=1)
        self.assertIn("unknown task_id", completed.stderr + completed.stdout)

    def test_run_without_an_endpoint_fails_clearly(self):
        completed = self.cli("run", "--task", AUTO_INGEST_BUG_FIX, expect=1)
        combined = completed.stderr + completed.stdout
        self.assertIn("no endpoint configured", combined)

    def test_review_stage_requires_a_patch_file(self):
        completed = self.cli("run", "--task", AUTO_INGEST_BUG_FIX, "--stage", "review", expect=1)
        self.assertIn("--patch-file", completed.stderr + completed.stdout)


class PlanCommandTests(CliTestCase):
    def test_plan_command_prints_a_runnable_invocation(self):
        completed = self.cli(
            "plan-command",
            "--task", AUTO_INGEST_BUG_FIX,
            "--base-url", "http://127.0.0.1:9/v1",
            "--model", "test-model",
            "--label", "labelled-by-operator",
            "--out", str(self.tmp),
        )
        lines = [ln for ln in completed.stdout.splitlines() if ln.startswith("cd ")]
        self.assertEqual(len(lines), 1)
        self.assertIn("realtime_bench.py run", lines[0])
        self.assertIn("--task " + AUTO_INGEST_BUG_FIX, lines[0])
        self.assertIn("--stage single", lines[0])
        self.assertIn("--stage swarm", lines[0])
        self.assertIn("labelled-by-operator", lines[0])

    def test_the_printed_command_is_accepted_by_the_parser(self):
        from realtime_bench import build_parser

        completed = self.cli(
            "plan-command",
            "--task", AUTO_INGEST_BUG_FIX,
            "--base-url", "http://127.0.0.1:9/v1",
            "--model", "test-model",
            "--out", str(self.tmp),
        )
        command = [ln for ln in completed.stdout.splitlines() if ln.startswith("cd ")][0]
        tokens = command.split("&& ", 1)[1].split()
        argv = tokens[2:]  # drop the interpreter and the script path
        args = build_parser().parse_args(argv)
        self.assertEqual(args.command, "run")
        self.assertEqual(args.task, [AUTO_INGEST_BUG_FIX])
        self.assertEqual(args.stage, ["single", "swarm"])


class RunAgainstLoopbackTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.endpoint = LoopbackEndpoint(self.role_replies())
        self.addCleanup(self.endpoint.close)
        self.out = self.tmp / "runs"

    @staticmethod
    def role_replies():
        return {
            "single": patch_payload(),
            "scout": scout_payload(),
            "implementer": patch_payload(),
            "reviewer": review_payload(),
        }

    def run_cli(self, *extra: str, task: str = AUTO_INGEST_BUG_FIX, expect: int = 0):
        return self.cli(
            "run",
            "--task", task,
            "--out", str(self.out),
            "--base-url", self.endpoint.base_url,
            "--model", "loopback-test-model",
            "--label", "loopback",
            "--node", "unit-test",
            *extra,
            expect=expect,
        )

    def run_dirs(self):
        return sorted(p for p in self.out.iterdir() if p.is_dir()) if self.out.is_dir() else []

    def test_single_and_swarm_produce_a_complete_run_directory(self):
        self.run_cli("--stage", "single", "--stage", "swarm")
        run_dir = self.run_dirs()[0]

        for name in (
            "manifest.json", "task.json", "source-manifest.json", "metrics.json",
            "test-results.json", "patch.diff",
        ):
            self.assertTrue((run_dir / name).is_file(), name)
        self.assertTrue((run_dir / "single" / "result.json").is_file())
        self.assertTrue((run_dir / "swarm" / "result.json").is_file())
        comparison = run_dir / "comparison-{}.json".format(AUTO_INGEST_BUG_FIX)
        self.assertTrue(comparison.is_file())

    def test_manifest_records_full_provenance(self):
        self.run_cli("--stage", "single")
        run_dir = self.run_dirs()[0]
        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertEqual(manifest["schema"], "realtask.run_manifest.v1")
        self.assertEqual(len(manifest["harness"]["git_sha"]), 40)
        self.assertTrue(manifest["fixture"]["fixture_sha256"])
        endpoint = manifest["model_runtime"][0]
        self.assertEqual(endpoint["label"], "loopback")
        self.assertEqual(endpoint["model"], "loopback-test-model")
        self.assertEqual(endpoint["node"], "unit-test")
        self.assertFalse(endpoint["api_key_set"])
        self.assertTrue(manifest["started_at"])
        self.assertTrue(manifest["finalized_at"])
        self.assertIn("metrics.json", manifest["artifacts_written"])
        self.assertTrue(manifest["integrity"]["authoritative_source_unchanged"])
        self.assertFalse(manifest["authority"]["authoritative_repo_mutated"])
        self.assertFalse(manifest["authority"]["assistx_task_state_mutated"])
        self.assertFalse(manifest["authority"]["routing_or_admission_mutated"])

    def test_metrics_capture_ttft_and_throughput_from_a_real_stream(self):
        self.run_cli("--stage", "single")
        run_dir = self.run_dirs()[0]
        metrics = json.loads((run_dir / "metrics.json").read_text())
        call = metrics["attempts"][0]["calls"][0]
        self.assertTrue(call["stream_used"])
        self.assertIsNotNone(call["ttft_s"])
        self.assertGreaterEqual(call["ttft_s"], 0.0)
        self.assertTrue(call["usage_reported"])
        self.assertEqual(call["prompt_tokens"], 4242)
        self.assertEqual(call["completion_tokens"], 77)
        self.assertEqual(metrics["attempts"][0]["total_tokens"], 4319)
        self.assertIsNotNone(call["tokens_per_s"])
        self.assertEqual(call["server_timings"]["predicted_per_second"], 11.5)
        self.assertEqual(metrics["attempts"][0]["outcome"], "SUCCESS")

    def test_streams_when_asked_not_to(self):
        self.run_cli("--stage", "single", "--no-stream")
        run_dir = self.run_dirs()[0]
        metrics = json.loads((run_dir / "metrics.json").read_text())
        call = metrics["attempts"][0]["calls"][0]
        self.assertFalse(call["stream_used"])
        self.assertIsNone(call["ttft_s"])
        self.assertEqual(call["completion_tokens"], 77)

    def test_swarm_calls_the_endpoint_in_role_order(self):
        self.run_cli("--stage", "swarm")
        run_dir = self.run_dirs()[0]
        metrics = json.loads((run_dir / "metrics.json").read_text())
        self.assertEqual(
            [c["role"] for c in metrics["attempts"][0]["calls"]],
            ["scout", "implementer", "reviewer"],
        )
        self.assertEqual(len(self.endpoint.requests), 3)
        for request in self.endpoint.requests:
            blob = "\n".join(m["content"] for m in request["messages"])
            self.assertIn("NO tools", blob, "every request must state the read-only contract")
            self.assertEqual(request["model"], "loopback-test-model")
            self.assertEqual(request["temperature"], 0.0)
            self.assertEqual(request["seed"], 13)
        reviewer_blob = "\n".join(m["content"] for m in self.endpoint.requests[2]["messages"])
        self.assertIn("SOURCE_BINDING (verified by the harness", reviewer_blob)
        self.assertIn("CANDIDATE PATCH (verbatim", reviewer_blob)
        self.assertIn("d7d75fff9e97e6f27056677205a75cbcb49ca48d", reviewer_blob)

    def test_comparison_artifact_is_component_wise(self):
        self.run_cli("--stage", "single", "--stage", "swarm")
        run_dir = self.run_dirs()[0]
        comparison = json.loads(
            (run_dir / "comparison-{}.json".format(AUTO_INGEST_BUG_FIX)).read_text()
        )
        self.assertEqual(comparison["schema"], "realtask.comparison.v1")
        self.assertEqual(comparison["quality_difference"]["composite_score"], None)
        components = comparison["quality_difference"]["components"]
        for key in ("grounding_score", "targeted_tests_passed", "review_defects_total",
                    "unnecessary_changed_file_count", "refinements_used"):
            self.assertIn(key, components, key)
        cost = comparison["cost_difference"]
        self.assertEqual(cost["model_calls"]["single"], 1)
        self.assertEqual(cost["model_calls"]["swarm"], 3)
        self.assertIn("harness_owned_seconds", comparison["controller_integration_overhead"])
        self.assertTrue(comparison["scope_limits"])

    def test_analysis_task_runs_through_its_answer_check(self):
        endpoint = LoopbackEndpoint(
            {"scout": analysis_payload(), "reviewer": review_payload()}
        )
        self.addCleanup(endpoint.close)
        completed = self.cli(
            "run",
            "--task", AUTO_INGEST_CONTRACT,
            "--stage", "swarm",
            "--out", str(self.tmp / "analysis"),
            "--base-url", endpoint.base_url,
            "--model", "loopback-test-model",
        )
        self.assertIn("SUCCESS", completed.stdout)

    def test_run_never_writes_outside_its_output_root(self):
        before = {
            str(p): p.stat().st_mtime
            for p in (REPO_ROOT / "realtask").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }
        self.run_cli("--stage", "single")
        after = {
            str(p): p.stat().st_mtime
            for p in (REPO_ROOT / "realtask").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }
        self.assertEqual(before, after, "the run modified the fixture corpus")


if __name__ == "__main__":
    unittest.main()
