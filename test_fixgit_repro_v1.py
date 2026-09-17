import contextlib
import io
import json
import tempfile
import time
import subprocess
import sys
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import fixgit_repro_v1 as harness


class SetupFailureTests(unittest.TestCase):
    def test_setup_failure_writes_artifacts_and_removes_owned_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            stage = base / "stage"
            stage.mkdir()
            output = base / "results.json"
            with patch("sys.argv", ["probe", "--out", str(output)]), \
                    patch.object(harness.tempfile, "mkdtemp", return_value=str(stage)), \
                    patch.object(harness, "run_process", side_effect=FileNotFoundError("git unavailable")), \
                    contextlib.redirect_stdout(io.StringIO()):
                status = harness.main()
            self.assertEqual(status, 1)
            result = json.loads(output.read_text())
            self.assertFalse(result["passed"])
            self.assertEqual(result["steps"], 0)
            self.assertIn("FileNotFoundError", result["harness_error"])
            self.assertEqual((base / "probe.jsonl").read_text(), "")
            self.assertFalse(stage.exists())

    def test_setup_failure_preserves_caller_owned_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            stage = base / "stage"
            stage.mkdir()
            marker = stage / "keep.txt"
            marker.write_text("keep")
            output = base / "results.json"
            with patch("sys.argv", ["probe", "--root", str(stage), "--out", str(output)]), \
                    patch.object(harness, "run_process", side_effect=OSError("setup failed")), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(harness.main(), 1)
            self.assertEqual(marker.read_text(), "keep")
            self.assertFalse(json.loads(output.read_text())["passed"])


class RecoveryTests(unittest.TestCase):
    def run_scripted_recovery(self, commands):
        requests = []
        if isinstance(commands, str):
            commands = [commands]

        def reply(request):
            body = json.loads(request.data)
            requests.append(body)
            command = commands[min(len(requests) - 1, len(commands) - 1)]
            if callable(command):
                command = command(body)
            message = {
                "role": "assistant", "content": "",
                "tool_calls": [{
                    "id": f"recovery-{len(requests)}", "type": "function",
                    "function": {"name": "bash", "arguments": json.dumps({"command": command})},
                }],
            }
            return io.BytesIO(json.dumps({"choices": [{"message": message}]}).encode())

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.json"
            with patch("sys.argv", ["probe", "--tool-mode", "native", "--model", "test-model",
                                    "--max-steps", "2", "--timeout-s", "5", "--out", str(output)]), \
                    patch("urllib.request.urlopen", side_effect=reply), \
                    contextlib.redirect_stdout(io.StringIO()):
                status = harness.main()
            result = json.loads(output.read_text())
            trace = (output.parent / "probe.jsonl").read_text().splitlines()
        return status, result, requests, trace

    def test_recovers_from_reflog_observed_sha(self):
        def recover(body):
            output = body["messages"][-1]["content"]
            line = next(line for line in output.splitlines() if "commit: Feature work" in line)
            sha = line.split()[0]
            self.assertEqual(len(sha), 40)
            self.assertTrue(all(c in "0123456789abcdef" for c in sha))
            return f"git branch recovery-branch {sha} && git merge --ff-only recovery-branch"

        status, result, requests, trace = self.run_scripted_recovery([
            "git reflog --format='%H %gs'",
            recover,
        ])
        self.assertEqual(status, 0)
        self.assertTrue(result["passed"])
        self.assertEqual(result["steps"], 2)
        self.assertEqual(len(requests), 2)
        second_request = requests[1]
        self.assertEqual(second_request["messages"][-1]["role"], "tool")
        self.assertEqual(second_request["messages"][-1]["tool_call_id"], "recovery-1")
        self.assertIn("commit: Feature work", second_request["messages"][-1]["content"])
        entries = [json.loads(line) for line in trace]
        self.assertEqual(entries[0]["request"], requests[0])
        self.assertEqual(entries[1]["request"], requests[1])
        self.assertEqual(entries[0]["tool_result"]["returncode"], 0)
        self.assertIn("commit: Feature work", entries[0]["tool_result"]["stdout"])
        self.assertEqual(entries[1]["response"]["choices"][0]["message"]["tool_calls"][0]["id"], "recovery-2")
        self.assertTrue(entries[1]["verification"]["master_contains_lost"])
        self.assertEqual(result["tool_mode"], "native")

    def test_lost_commit_is_only_reachable_through_reflog(self):
        status, result, requests, trace = self.run_scripted_recovery(
            "git branch --contains HEAD@{1}; git log --all --format=%s; git reflog --format=%gs"
        )
        feedback = requests[1]["messages"][-1]["content"]
        self.assertNotIn("feature", feedback)
        self.assertNotIn("\nFeature work\n", feedback)
        self.assertIn("commit: Feature work", feedback)
        self.assertFalse(result["passed"])

    def test_stops_after_successful_native_recovery(self):
        status, result, requests, trace = self.run_scripted_recovery(
            "git branch recovery-branch HEAD@{1} && git merge --ff-only recovery-branch"
        )
        self.assertEqual(status, 0)
        self.assertTrue(result["passed"])
        self.assertEqual(result["steps"], 1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(trace), 1)
        self.assertEqual(requests[0]["model"], "test-model")
        self.assertIsNone(result["harness_error"])

    def test_packed_recovery_ref_passes(self):
        status, result, requests, trace = self.run_scripted_recovery(
            "git branch recovery-branch HEAD@{1} && git merge --ff-only recovery-branch && git pack-refs --all"
        )
        self.assertEqual(status, 0)
        self.assertTrue(result["signal"]["recovery_branch_exists"])
        self.assertEqual(len(requests), 1)

    def test_incomplete_recovery_does_not_pass(self):
        for command in (
            "git branch recovery-branch HEAD@{1}",
            "git merge --ff-only HEAD@{1}",
            "git branch recovery-branch master && git merge --ff-only HEAD@{1}",
        ):
            with self.subTest(command=command):
                status, result, requests, trace = self.run_scripted_recovery(command)
                self.assertEqual(status, 1)
                self.assertFalse(result["passed"])
                self.assertEqual(len(requests), 2)
                self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
                self.assertEqual(requests[1]["messages"][-1]["tool_call_id"], "recovery-1")


class DeadlineTests(unittest.TestCase):
    def test_expired_budget_does_not_start_process(self):
        with patch.object(harness.subprocess, "Popen") as spawn:
            with self.assertRaises(TimeoutError):
                harness.run_process(["true"], time.monotonic() - 1)
            spawn.assert_not_called()

    def test_timeout_kills_child_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child-finished"
            child = "import time,pathlib; time.sleep(1.5); pathlib.Path(%r).touch()" % str(marker)
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', %r]); print('started', flush=True); time.sleep(30)" % child
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                harness.run_process([sys.executable, "-c", parent], started + 0.4)
            self.assertLess(time.monotonic() - started, 1.4)
            self.assertIn("started", caught.exception.output)
            time.sleep(1.6)
            self.assertFalse(marker.exists())

    def test_http_budget_interrupts_slow_read(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            with harness.request_budget(started + 0.1):
                time.sleep(2)
        self.assertLess(time.monotonic() - started, 1)

    def test_slow_http_response_reports_deadline_and_keeps_trace(self):
        release = threading.Event()
        received = threading.Event()
        class SlowHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.flush()
                received.set()
                release.wait(5)

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "results.json"
                endpoint = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
                started = time.monotonic()
                with patch("sys.argv", ["probe", "--endpoint", endpoint,
                                        "--timeout-s", "1", "--out", str(output)]), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(harness.main(), 1)
                self.assertTrue(received.is_set())
                self.assertLess(time.monotonic() - started, 2.5)
                result = json.loads(output.read_text())
                self.assertFalse(result["passed"])
                self.assertTrue(result["deadline_exceeded"])
                self.assertIn("deadline", result["harness_error"].lower())
                self.assertGreaterEqual(result["elapsed_s"], 0.9)
                self.assertIn("deadline", (output.parent / "probe.jsonl").read_text().lower())
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_setup_timeout_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.json"
            started = time.monotonic()
            original = harness.run_process
            def slow_git(command, deadline, **kwargs):
                return original([sys.executable, "-c", "import time; time.sleep(30)"], deadline)
            with patch("sys.argv", ["probe", "--timeout-s", "0.2", "--out", str(output)]), \
                    patch.object(harness, "run_process", side_effect=slow_git), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(harness.main(), 1)
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertFalse(json.loads(output.read_text())["passed"])
            self.assertTrue((output.parent / "probe.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
