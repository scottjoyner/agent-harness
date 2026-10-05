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
import unittest.mock
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from test_realtask_support import (
    AUTO_INGEST_BUG_FIX,
    TASKS_ROOT,
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


def review_payload(verdict: str = "accept") -> str:
    return json.dumps(
        {
            "defects": [
                {"severity": "low", "location": "cli.py:71", "description": "no lifecycle test"}
            ],
            "missing_coverage": ["driver liveness regression test"],
            "contract_violations": [],
            "verdict": verdict,
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

    def run_dirs(self):
        """Evidence run directories, excluding harness scratch directories."""
        out = getattr(self, "out", None)
        if out is None or not out.is_dir():
            return []
        return sorted(
            p for p in out.iterdir() if p.is_dir() and (p / "manifest.json").is_file()
        )

    def single_run_dir(self):
        return self.run_dirs()[0]


class ValidateAndListTests(CliTestCase):
    def test_validate_all_fixtures_without_an_endpoint(self):
        from realtask.fixtures import iter_fixture_manifests

        payload = self.cli_json("validate", "--json")
        self.assertEqual(payload["failures"], 0)
        # Count against the corpus on disk rather than a literal, so adding a
        # fixture cannot leave this test asserting a number nobody maintains.
        on_disk = {
            m.parent.name for m in iter_fixture_manifests(TASKS_ROOT)
        }
        self.assertEqual(payload["count"], len(on_disk))
        self.assertEqual({row["task_id"] for row in payload["tasks"]}, on_disk)
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


class StandaloneStageTests(CliTestCase):
    """Each stage must be runnable on its own, not only inside `swarm`."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "runs"

    def endpoint(self, by_role):
        server = LoopbackEndpoint(by_role)
        self.addCleanup(server.close)
        return server

    def run_stage(self, stage, server, *extra):
        return self.cli(
            "run",
            "--task", AUTO_INGEST_BUG_FIX,
            "--stage", stage,
            "--out", str(self.out),
            "--base-url", server.base_url,
            "--model", "loopback-test-model",
            *extra,
        )

    def test_scout_stage_runs_alone(self):
        server = self.endpoint({"scout": scout_payload()})
        completed = self.run_stage("scout", server)
        self.assertIn("outcome=", completed.stdout)
        run_dir = self.single_run_dir()
        self.assertTrue((run_dir / "scout" / "result.json").is_file())
        result = json.loads((run_dir / "scout" / "result.json").read_text())
        self.assertEqual(result["strategy"], "scout")
        self.assertIn("root_cause", result["scout"])
        self.assertNotIn("implementer", result)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(len(result["scout"]["relevant_files"]), 1)

    def test_implement_stage_runs_alone_and_is_evaluated(self):
        server = self.endpoint({"implementer": patch_payload()})
        self.run_stage("implement", server)
        run_dir = self.single_run_dir()
        result = json.loads((run_dir / "implementer" / "result.json").read_text())
        self.assertEqual(result["strategy"], "implement")
        self.assertIn("patch", result["implementer"])
        self.assertEqual(result["outcome"], "SUCCESS")
        self.assertTrue((run_dir / "patch.diff").is_file())
        self.assertTrue((run_dir / "test-results.json").is_file())

    def test_an_operator_patch_is_recorded_in_the_manifest(self):
        """Provenance has to survive to disk, not just to memory.

        Hashed rather than merely named: a path can be rewritten between runs, so
        the record must identify *which* candidate was judged.
        """
        import hashlib

        server = self.endpoint({"reviewer": review_payload()})
        patch_file = self.tmp / "candidate.diff"
        patch_file.write_text(REFERENCE_REPAIR)
        self.run_stage("review", server, "--patch-file", str(patch_file))

        manifest = json.loads((self.run_dirs()[0] / "manifest.json").read_text())
        override = manifest["options"]["candidate_override"]
        self.assertIsNotNone(override, "no candidate provenance in the manifest")
        self.assertEqual(override["path"], str(patch_file))
        self.assertEqual(override["bytes"], len(REFERENCE_REPAIR.encode()))
        self.assertEqual(
            override["sha256"],
            hashlib.sha256(REFERENCE_REPAIR.encode()).hexdigest(),
        )

    def test_a_model_run_records_no_candidate_override(self):
        server = self.endpoint({"single": patch_payload()})
        self.run_stage("single", server)
        manifest = json.loads((self.run_dirs()[0] / "manifest.json").read_text())
        self.assertIsNone(manifest["options"]["candidate_override"])

    def test_review_stage_consumes_a_patch_file(self):
        server = self.endpoint({"reviewer": review_payload()})
        patch_file = self.tmp / "candidate.diff"
        patch_file.write_text(REFERENCE_REPAIR)
        self.run_stage("review", server, "--patch-file", str(patch_file))

        run_dir = self.single_run_dir()
        result = json.loads((run_dir / "reviewer" / "result.json").read_text())
        self.assertEqual(result["strategy"], "review")
        self.assertEqual(result["outcome"], "SUCCESS")
        self.assertEqual(result["reviewer"]["verdict"], "accept")
        reviewer_request = "\n".join(m["content"] for m in server.requests[0]["messages"])
        self.assertIn("CANDIDATE PATCH (verbatim", reviewer_request)
        self.assertIn("@@ -64,15 +64,16 @@ def _cmd_plan(args) -> int:", reviewer_request)
        self.assertIn("SOURCE_BINDING (verified by the harness", reviewer_request)
        self.assertEqual(result["binding"]["ok"], True)

    def test_review_stage_records_a_rejected_candidate(self):
        server = self.endpoint({"reviewer": review_payload("revise")})
        patch_file = self.tmp / "candidate.diff"
        patch_file.write_text(REFERENCE_REPAIR)
        completed = self.run_stage(
            "review", server, "--patch-file", str(patch_file), "--no-refinement"
        )
        self.assertIn("REVIEW_REJECTED", completed.stdout)
        result = json.loads((self.single_run_dir() / "reviewer" / "result.json").read_text())
        self.assertEqual(result["outcome"], "REVIEW_REJECTED")
        self.assertEqual(result["reviewer"]["verdict"], "revise")

    def test_review_stage_reports_an_unapplicable_candidate(self):
        from test_realtask_support import NON_APPLYING_PATCH

        server = self.endpoint({"reviewer": review_payload()})
        patch_file = self.tmp / "candidate.diff"
        patch_file.write_text(NON_APPLYING_PATCH)
        completed = self.run_stage("review", server, "--patch-file", str(patch_file))
        self.assertIn("PATCH_DOES_NOT_APPLY", completed.stdout)


class MultiTaskRunTests(CliTestCase):
    def test_two_tasks_in_one_run_get_separate_evidence(self):
        from test_realtask_support import AUTO_INGEST_CONTRACT as CONTRACT

        server = LoopbackEndpoint(
            {
                "single": patch_payload(),
                "scout": analysis_payload(),
                "implementer": patch_payload(),
                "reviewer": review_payload(),
            }
        )
        self.addCleanup(server.close)
        out = self.tmp / "multi"
        completed = self.cli(
            "run",
            "--task", AUTO_INGEST_BUG_FIX,
            "--task", CONTRACT,
            "--stage", "single",
            "--out", str(out),
            "--base-url", server.base_url,
            "--model", "loopback-test-model",
        )
        self.assertIn(AUTO_INGEST_BUG_FIX, completed.stdout)
        self.assertIn(CONTRACT, completed.stdout)

        run_dirs = sorted(
            p for p in out.iterdir() if p.is_dir() and (p / "manifest.json").is_file()
        )
        self.assertEqual(len(run_dirs), 1)
        run_dir = run_dirs[0]
        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertIn(AUTO_INGEST_BUG_FIX, manifest["fixture"]["task_id"])
        self.assertIn(CONTRACT, manifest["fixture"]["task_id"])
        self.assertEqual(set(manifest["outcomes"]), {AUTO_INGEST_BUG_FIX, CONTRACT})

        # Each task gets its own evidence directory; nothing is overwritten.
        for task_id in (AUTO_INGEST_BUG_FIX, CONTRACT):
            task_dir = run_dir / "tasks" / task_id
            with self.subTest(task=task_id):
                self.assertTrue((task_dir / "task.json").is_file())
                self.assertTrue((task_dir / "metrics.json").is_file())
                self.assertTrue((task_dir / "test-results.json").is_file())
                self.assertTrue(
                    (task_dir / "comparison.json").is_file(),
                    "each task gets its own comparison",
                )
                payload = json.loads((task_dir / "task.json").read_text())
                self.assertEqual(payload["task_id"], task_id)
                self.assertEqual(payload["schema"], "realtask.task.v1")
                metrics = json.loads((task_dir / "metrics.json").read_text())
                self.assertEqual(metrics["task_id"], task_id)

        # The manifest indexes the nested evidence.
        indexed = manifest["artifacts_written"]
        for expected in (
            "tasks/{}/task.json".format(AUTO_INGEST_BUG_FIX),
            "tasks/{}/comparison.json".format(CONTRACT),
            "tasks/{}/metrics.json".format(CONTRACT),
        ):
            self.assertIn(expected, indexed, expected)

        # A single-task run keeps the flat documented layout.
        single_out = self.tmp / "single-layout"
        single_server = LoopbackEndpoint({"single": patch_payload()})
        self.addCleanup(single_server.close)
        self.cli(
            "run", "--task", AUTO_INGEST_BUG_FIX, "--stage", "single",
            "--out", str(single_out),
            "--base-url", single_server.base_url, "--model", "loopback-test-model",
        )
        only = sorted(
            p for p in single_out.iterdir()
            if p.is_dir() and (p / "manifest.json").is_file()
        )[0]
        self.assertTrue((only / "task.json").is_file())
        self.assertTrue((only / "comparison.json").is_file())
        self.assertFalse((only / "tasks").exists())

        self.assertTrue(manifest["integrity"]["authoritative_source_unchanged"])
        # Scratch work trees never appear inside the evidence root.
        self.assertFalse(any(p.name.endswith(".work") for p in out.iterdir()))
        self.assertEqual(
            sorted(p.name for p in out.iterdir() if p.is_dir()),
            [run_dir.name],
            "the evidence root holds runs and nothing else",
        )


class ScoutHandoffTests(CliTestCase):
    """A scout run must be able to feed a later implementer run.

    Without this, role separation cannot be inspected between invocations: you
    could run a scout, read it, and then only re-ask the model.
    """

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "handoff"
        self.server = LoopbackEndpoint(
            {"scout": scout_payload(), "implementer": patch_payload()}
        )
        self.addCleanup(self.server.close)

    def run_cli(self, *args, expect=0):
        return self.cli(
            "run", "--task", AUTO_INGEST_BUG_FIX, "--out", str(self.out),
            "--base-url", self.server.base_url, "--model", "loopback-test-model",
            *args, expect=expect,
        )

    def make_scout(self):
        self.run_cli("--stage", "scout", "--run-id", "step1")
        return self.out / "step1" / "scout" / "result.json"

    def test_scout_then_implement_across_invocations(self):
        scout_path = self.make_scout()
        self.assertTrue(scout_path.is_file())
        completed = self.run_cli(
            "--stage", "implement", "--run-id", "step2",
            "--scout-file", str(scout_path),
        )
        self.assertIn("implement", completed.stdout)
        self.assertIn("SUCCESS", completed.stdout)

    def test_handoff_is_recorded_in_the_manifest(self):
        scout_path = self.make_scout()
        self.run_cli("--stage", "implement", "--run-id", "step2",
                     "--scout-file", str(scout_path))
        manifest = json.loads((self.out / "step2" / "manifest.json").read_text())
        handoff = manifest["options"]["scout_handoff"]
        self.assertEqual(handoff["source_strategy"], "scout")
        self.assertEqual(handoff["source_attempt_id"],
                         "{}::scout".format(AUTO_INGEST_BUG_FIX))
        self.assertEqual(handoff["relevant_files"], ["auto_ingest/shorts/cli.py"])
        self.assertEqual(handoff["confidence"], 0.8)

    def test_the_scout_actually_reaches_the_implementer_prompt(self):
        """The whole point: the implementer sees the scout, not just the task."""
        from realtask.adapter import ChatRequest, OpenAIChatAdapter
        from realtask.evidence import RunDirectory
        from realtask.fixtures import load_task_by_id
        from realtask.roles import scout_result_from_evidence
        from realtask.runner import BenchmarkRunner, RunnerOptions

        scout_path = self.make_scout()
        task = load_task_by_id(AUTO_INGEST_BUG_FIX, TASKS_ROOT)
        payload = json.loads(scout_path.read_text())
        scout = scout_result_from_evidence(payload["scout"])

        captured = {}

        class Capture(OpenAIChatAdapter):
            def complete(self, request: ChatRequest):
                captured["user"] = request.user
                return super().complete(request)

        adapter = Capture(
            __import__("realtask.adapter", fromlist=["EndpointConfig"]).EndpointConfig(
                label="cap", base_url=self.server.base_url, model="loopback-test-model",
                timeout_s=30, retry_backoff_s=0.0,
            )
        )
        runner = BenchmarkRunner(
            adapter, RunDirectory(self.tmp / "cap", "cap"),
            RunnerOptions(test_timeout_s=180.0),
            work_root=self.tmp / "capwork", harness_root=REPO_ROOT,
        )
        try:
            runner.run_task(task, ["implement"], scout_override=scout)
        finally:
            runner.close()
        self.assertIn("SCOUT REPORT", captured["user"])
        self.assertIn(scout.root_cause, captured["user"])
        self.assertIn(scout.relevant_files[0], captured["user"])

    def test_missing_file_is_rejected(self):
        completed = self.run_cli(
            "--stage", "implement", "--scout-file", str(self.out / "nope.json"), expect=1
        )
        self.assertIn("--scout-file not found", completed.stderr)

    def test_wrong_schema_is_rejected(self):
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps({"schema": "bench_v7.unversioned", "scout": {}}))
        completed = self.run_cli("--stage", "implement", "--scout-file", str(bad), expect=1)
        self.assertIn("expected", completed.stderr)
        self.assertIn("realtask.role_result.v1", completed.stderr)

    def test_missing_scout_block_is_rejected(self):
        bad = self.tmp / "no-scout.json"
        bad.write_text(json.dumps({"schema": "realtask.role_result.v1",
                                   "strategy": "single"}))
        completed = self.run_cli("--stage", "implement", "--scout-file", str(bad), expect=1)
        self.assertIn("no 'scout' block", completed.stderr)

    def test_malformed_scout_block_is_rejected(self):
        bad = self.tmp / "malformed.json"
        bad.write_text(json.dumps({
            "schema": "realtask.role_result.v1",
            "scout": {"root_cause": "x"},
        }))
        completed = self.run_cli("--stage", "implement", "--scout-file", str(bad), expect=1)
        self.assertIn("malformed", completed.stderr)

    def test_invalid_json_is_rejected(self):
        bad = self.tmp / "broken.json"
        bad.write_text("{not json")
        completed = self.run_cli("--stage", "implement", "--scout-file", str(bad), expect=1)
        self.assertIn("not valid JSON", completed.stderr)

    def test_unusable_stage_warns_rather_than_silently_ignoring(self):
        scout_path = self.make_scout()
        completed = self.run_cli(
            "--stage", "single", "--scout-file", str(scout_path)
        )
        self.assertIn("warning: --scout-file only affects", completed.stderr)


class SummarizeCommandTests(CliTestCase):
    """summarize is the consumption surface: runs in, one roll-up out."""

    def test_summarize_needs_no_endpoint(self):
        # An empty runs root exits 1: no evidence is a configuration mistake,
        # not a result.
        payload = json.loads(
            self.cli("summarize", "--out", str(self.tmp / "empty"), "--json", expect=1).stdout
        )
        self.assertEqual(payload["schema"], "realtask.rollup.v1")
        self.assertEqual(payload["tasks"], [])
        self.assertIsNone(payload["composite_score"])

    def test_empty_runs_root_exits_one(self):
        self.cli("summarize", "--out", str(self.tmp / "empty"), expect=1)

    def test_summarize_folds_real_runs(self):
        server = LoopbackEndpoint({"single": patch_payload()})
        self.addCleanup(server.close)
        out = self.tmp / "runs"
        self.cli(
            "run", "--task", AUTO_INGEST_BUG_FIX, "--stage", "single",
            "--out", str(out), "--base-url", server.base_url, "--model", "loopback-test-model",
        )
        payload = self.cli_json("summarize", "--out", str(out), "--json")
        self.assertEqual(payload["aggregate"]["runs"], 1)
        self.assertEqual(payload["aggregate"]["tasks"], 1)
        self.assertEqual(payload["aggregate"]["outcome_histogram"], {"SUCCESS": 1})
        self.assertEqual(payload["tasks"][0]["task_id"], AUTO_INGEST_BUG_FIX)
        self.assertTrue((out / "rollup.json").is_file())

    def test_human_output_states_the_scope_limits(self):
        server = LoopbackEndpoint({"single": patch_payload()})
        self.addCleanup(server.close)
        out = self.tmp / "runs"
        self.cli(
            "run", "--task", AUTO_INGEST_BUG_FIX, "--stage", "single",
            "--out", str(out), "--base-url", server.base_url, "--model", "loopback-test-model",
        )
        completed = self.cli("summarize", "--out", str(out))
        self.assertIn("composite_score: None", completed.stdout)
        self.assertIn("does not establish that a model qualifies", completed.stdout)
        self.assertIn("=== by task family ===", completed.stdout)
        self.assertIn("=== by strategy ===", completed.stdout)

    def test_out_file_can_be_redirected(self):
        target = self.tmp / "elsewhere" / "rollup.json"
        self.cli("summarize", "--out", str(self.tmp / "empty"),
                 "--out-file", str(target), expect=1)
        self.assertTrue(target.is_file())

    def test_summarize_ignores_a_foreign_evidence_directory(self):
        """Legacy artifacts must not be silently folded into a roll-up."""
        out = self.tmp / "runs"
        legacy = out / "legacy-run"
        legacy.mkdir(parents=True)
        (legacy / "manifest.json").write_text(json.dumps({"schema": "bench_v7.unversioned"}))
        (legacy / "metrics.json").write_text(json.dumps({"schema": "whatever"}))
        payload = json.loads(
            self.cli("summarize", "--out", str(out), "--json", expect=1).stdout
        )
        self.assertEqual(payload["aggregate"]["runs"], 0)
        reasons = " ".join(s["reason"] for s in payload["skipped_runs"])
        self.assertIn("manifest schema", reasons)


class ExitCodeTests(CliTestCase):
    """The exit code must distinguish a bad model from a bad harness."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "runs"

    def test_clean_run_exits_zero(self):
        server = LoopbackEndpoint({"single": patch_payload()})
        self.addCleanup(server.close)
        self.cli(
            "run", "--task", AUTO_INGEST_BUG_FIX, "--stage", "single",
            "--out", str(self.out), "--base-url", server.base_url, "--model", "m",
        )

    def test_model_failure_is_not_a_harness_failure(self):
        """A model that produces nothing usable is evidence, not a harness bug."""
        server = LoopbackEndpoint({"single": "I am not JSON and there is no diff."})
        self.addCleanup(server.close)
        completed = self.cli(
            "run", "--task", AUTO_INGEST_BUG_FIX, "--stage", "single",
            "--out", str(self.out), "--base-url", server.base_url, "--model", "m",
        )
        self.assertNotIn("HARNESS ERROR", completed.stderr)
        self.assertIn("PROTOCOL_FAILURE", completed.stdout)
        manifest = json.loads(
            (self.single_run_dir() / "manifest.json").read_text()
        )
        self.assertEqual(manifest["integrity"]["harness_errors"], [])

    def test_harness_error_exits_three_but_still_writes_evidence(self):
        """An unexpected harness failure is exit 3, and evidence is intact.

        Driven in-process rather than by corrupting a source file: the contract
        under test is the CLI's reporting, not the fault itself. How a harness
        fault actually arises is covered in test_realtask_runner.
        """
        import contextlib
        import io

        import realtime_bench
        from realtask.runner import BenchmarkRunner

        server = LoopbackEndpoint({"single": patch_payload()})
        self.addCleanup(server.close)
        real_run_task = BenchmarkRunner.run_task

        def faulty(self, task, strategies, **kwargs):
            result = real_run_task(self, task, strategies, **kwargs)
            for state in result.attempts:
                state.metrics.harness_error = "InjectedError: simulated fault"
            return result

        argv = [
            "run", "--task", AUTO_INGEST_BUG_FIX, "--stage", "single",
            "--out", str(self.out), "--base-url", server.base_url, "--model", "m",
        ]
        buffer = io.StringIO()
        with unittest.mock.patch.object(BenchmarkRunner, "run_task", faulty):
            with contextlib.redirect_stderr(buffer):
                code = realtime_bench.main(argv)
        stderr = buffer.getvalue()
        self.assertEqual(code, 3)
        self.assertIn("HARNESS ERROR: InjectedError", stderr)
        self.assertIn("Exit 3", stderr)

        run_dir = sorted(
            p for p in self.out.iterdir() if p.is_dir() and (p / "manifest.json").is_file()
        )[0]
        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertTrue(manifest["integrity"]["harness_errors"])
        self.assertTrue(manifest["integrity"]["authoritative_source_unchanged"])
        # The run is flagged, not erased: normal evidence is still on disk.
        self.assertTrue((run_dir / "metrics.json").is_file())
        self.assertTrue((run_dir / "single" / "metrics.json").is_file())


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
        self.assertTrue((run_dir / "comparison.json").is_file())
        self.assertTrue((run_dir / "source-manifest.verified.json").is_file())

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
        self.assertIn("comparison.json", manifest["artifacts_written"])
        self.assertIn("single/result.json", manifest["artifacts_written"])
        self.assertTrue(manifest["integrity"]["authoritative_source_unchanged"])
        # Scratch work trees are removed and never left inside the evidence root.
        self.assertFalse(
            [p.name for p in self.out.iterdir() if p.name.endswith(".work")],
            "a .work scratch directory survived the run",
        )
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
        comparison = json.loads((run_dir / "comparison.json").read_text())
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
