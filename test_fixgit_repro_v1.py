import contextlib
import io
import json
import os
import re
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
    def run_scripted_recovery(self, commands, snapshot_after_first_command=False,
                              system=None, per_turn_task=False, extra=None):
        requests = []
        trace_during_run = None
        if isinstance(commands, str):
            commands = [commands]

        def reply(request):
            body = json.loads(request.data)
            requests.append(body)
            if snapshot_after_first_command and len(requests) == 2:
                nonlocal trace_during_run
                trace_during_run = (output.parent / "probe.jsonl").read_text()
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
            argv = ["probe", "--tool-mode", "native", "--model", "test-model",
                    "--max-steps", "2", "--timeout-s", "5", "--out", str(output)]
            if system is not None:
                argv += ["--system-prompt", system]
            if per_turn_task:
                argv += ["--per-turn-task"]
            if extra is not None:
                argv += ["--extra-task", extra]
            with patch("sys.argv", argv), \
                    patch("urllib.request.urlopen", side_effect=reply), \
                    contextlib.redirect_stdout(io.StringIO()):
                status = harness.main()
            result = json.loads(output.read_text())
            trace = (output.parent / "probe.jsonl").read_text().splitlines()
        return status, result, requests, trace, trace_during_run

    def test_completed_turn_is_on_disk_before_next_request(self):
        status, result, requests, trace, snapshot = self.run_scripted_recovery(
            "git reflog --format='%H %gs'", snapshot_after_first_command=True
        )
        self.assertIsNotNone(snapshot)
        entries = [json.loads(line) for line in snapshot.splitlines()]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tool_result"]["returncode"], 0)
        self.assertIn("commit: Feature work", entries[0]["tool_result"]["stdout"])
        self.assertFalse(entries[0]["verification"]["master_contains_lost"])

    def test_system_prompt_is_sent_when_provided(self):
        system = "Act on the first decisive evidence; do not rescan."
        status, result, requests, trace, _ = self.run_scripted_recovery(
            ["git reflog --format='%H %gs'",
             "git branch recovery-branch HEAD@{1} && git merge --ff-only recovery-branch"],
            system=system,
        )
        self.assertEqual(status, 0)
        self.assertEqual(requests[0]["messages"][0],
                         {"role": "system", "content": system})
        self.assertEqual(requests[1]["messages"][0],
                         {"role": "system", "content": system})

    def test_no_system_prompt_by_default(self):
        status, result, requests, trace, _ = self.run_scripted_recovery(
            "git branch recovery-branch HEAD@{1} && git merge --ff-only recovery-branch"
        )
        self.assertEqual(requests[0]["messages"][0]["role"], "user")

    def test_per_turn_task_restatement_includes_goal(self):
        status, result, requests, trace, _ = self.run_scripted_recovery(
            ["git reflog --format='%H %gs'",
             "git branch recovery-branch HEAD@{1} && git merge --ff-only recovery-branch"],
            per_turn_task=True,
        )
        self.assertEqual(status, 0)
        messages = requests[1]["messages"]
        self.assertEqual([m["role"] for m in messages],
                         ["user", "assistant", "tool", "user"])
        call = messages[1]["tool_calls"][0]
        feedback = messages[2]
        self.assertEqual(feedback["tool_call_id"], call["id"])
        self.assertIn("Tool result rc=0", feedback["content"])
        self.assertIn("commit: Feature work", feedback["content"])
        goal = messages[-1]["content"]
        self.assertIn("recovery-branch", goal)
        self.assertIn("merge", goal)
        self.assertNotIn("Tool result rc=0", goal)
        self.assertEqual(json.loads(trace[1])["request"], requests[1])

    def test_per_turn_error_results_keep_native_linkage(self):
        original = harness.run_process

        def timeout_command(command, deadline, **kwargs):
            if command == ["bash", "-lc", "timeout-marker"]:
                raise subprocess.TimeoutExpired(command, 0.01, output="partial",
                                                stderr="interrupted")
            return original(command, deadline, **kwargs)

        for command, code, detail in [("exit 7", 7, ""),
                                       ("timeout-marker", 124, "interrupted")]:
            with self.subTest(command=command), \
                    patch.object(harness, "run_process", side_effect=timeout_command):
                status, result, requests, trace, _ = self.run_scripted_recovery(
                    [command, "git status"], per_turn_task=True)
            self.assertEqual(status, 1)
            messages = requests[1]["messages"]
            self.assertEqual([m["role"] for m in messages],
                             ["user", "assistant", "tool", "user"])
            self.assertEqual(messages[2]["tool_call_id"],
                             messages[1]["tool_calls"][0]["id"])
            self.assertIn(f"rc={code}", messages[2]["content"])
            self.assertIn(detail, messages[2]["content"])
            self.assertEqual(json.loads(trace[0])["tool_result"]["returncode"], code)

    def test_extra_task_is_appended_to_user_task(self):
        status, result, requests, trace, _ = self.run_scripted_recovery(
            "git branch recovery-branch HEAD@{1} && git merge --ff-only recovery-branch",
            extra="First run: git reflog.",
        )
        self.assertEqual(status, 0)
        self.assertIn("First run: git reflog.",
                      requests[0]["messages"][0]["content"])

    def test_recovers_from_reflog_observed_sha(self):
        def recover(body):
            output = body["messages"][-1]["content"]
            line = next(line for line in output.splitlines() if "commit: Feature work" in line)
            sha = line.split()[0]
            self.assertEqual(len(sha), 40)
            self.assertTrue(all(c in "0123456789abcdef" for c in sha))
            return f"git branch recovery-branch {sha} && git merge --ff-only recovery-branch"

        status, result, requests, trace, _ = self.run_scripted_recovery([
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
        status, result, requests, trace, _ = self.run_scripted_recovery(
            "git branch --contains HEAD@{1}; git log --all --format=%s; git reflog --format=%gs"
        )
        feedback = requests[1]["messages"][-1]["content"]
        self.assertNotIn("feature", feedback)
        self.assertNotIn("\nFeature work\n", feedback)
        self.assertIn("commit: Feature work", feedback)
        self.assertFalse(result["passed"])

    def test_stops_after_successful_native_recovery(self):
        status, result, requests, trace, _ = self.run_scripted_recovery(
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
        status, result, requests, trace, _ = self.run_scripted_recovery(
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
                status, result, requests, trace, _ = self.run_scripted_recovery(command)
                self.assertEqual(status, 1)
                self.assertFalse(result["passed"])
                self.assertEqual(len(requests), 2)
                self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
                self.assertEqual(requests[1]["messages"][-1]["tool_call_id"], "recovery-1")


class FeedbackTests(unittest.TestCase):
    def test_unexecuted_turn_never_reports_tool_success(self):
        cases = [
            ("transport", OSError("connection unavailable")),
            ("empty", {"content": ""}),
            ("invalid", {"content": "", "tool_calls": [{
                "id": "invalid-call", "type": "function",
                "function": {"name": "bash", "arguments": "not json"},
            }]}),
        ]
        for name, response in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                requests = []
                output = Path(directory) / "results.json"

                def reply(request):
                    requests.append(json.loads(request.data))
                    if len(requests) == 1 and isinstance(response, Exception):
                        raise response
                    message = response if len(requests) == 1 else {"content": ""}
                    return io.BytesIO(json.dumps({"choices": [{"message": message}]}).encode())

                with patch("sys.argv", ["probe", "--tool-mode", "native",
                                        "--max-steps", "2", "--timeout-s", "5",
                                        "--out", str(output)]), \
                        patch("urllib.request.urlopen", side_effect=reply), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(harness.main(), 1)
                self.assertEqual(len(requests), 2)
                feedback = requests[1]["messages"][-1]
                self.assertEqual(feedback["role"], "user")
                self.assertIn("No tool was executed", feedback["content"])
                self.assertNotIn("rc=0", feedback["content"])
                self.assertNotIn("//ExitEmpty//", json.dumps(requests[1]))
                self.assertFalse(any(message["role"] == "tool"
                                     for message in requests[1]["messages"]))
                result = json.loads(output.read_text())
                self.assertEqual(result["signal"]["total_tool_calls"], 0)
                entries = [json.loads(line) for line in
                           (output.parent / "probe.jsonl").read_text().splitlines()]
                self.assertNotIn("tool_result", entries[0])
                if name != "empty":
                    self.assertIsNotNone(entries[0]["error"])
                    self.assertIsNone(entries[0]["command"])


class FixtureAuditTests(unittest.TestCase):
    def stage_fixture(self, root):
        ident = ["-c", "user.email=h@l", "-c", "user.name=h"]

        def git(*args, check=True):
            return subprocess.run(["git", "-C", str(root), *args],
                                  capture_output=True, text=True, check=check)
        git("init", "-b", "master")
        (root / "index.html").write_text("<h1>old</h1>\n")
        git("add", "index.html")
        git(*ident, "commit", "-m", "Initial")
        git("checkout", "--detach", "HEAD")
        (root / "index.html").write_text("<h1>new work</h1>\n")
        git(*ident, "commit", "-am", "Feature work")
        lost = git("rev-parse", "HEAD").stdout.strip()
        git("checkout", "master")
        return lost

    def test_lost_commit_reconstructible_via_fsck_and_reflog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            lost = self.stage_fixture(root)
            self.assertNotEqual(lost, "")
            # oracle 1: fsck, independent of reflogs
            fsck = subprocess.run(["git", "-C", str(root), "fsck",
                                   "--unreachable", "--no-reflogs"],
                                  capture_output=True, text=True, check=True)
            unreachable = re.findall(r"unreachable commit ([0-9a-f]{40})", fsck.stdout)
            self.assertEqual(len(unreachable), 1)
            self.assertEqual(unreachable[0], lost)
            # oracle 2: lost-found artifact written under .git
            subprocess.run(["git", "-C", str(root), "fsck", "--lost-found",
                            "--no-reflogs"], capture_output=True, text=True, check=True)
            artifact = root / ".git" / "lost-found" / "commit" / lost
            self.assertTrue(artifact.exists())
            # oracle 3: reflog line, matched by message
            reflog = subprocess.run(["git", "-C", str(root), "reflog",
                                     "--format=%H %gs"],
                                    capture_output=True, text=True, check=True)
            feature = [line.split()[0] for line in reflog.stdout.splitlines()
                       if line.endswith("commit: Feature work")]
            self.assertEqual(feature, [lost])
            # recovery verified with the harness's own checker
            content = subprocess.run(["git", "-C", str(root), "show",
                                      f"{lost}:index.html"],
                                     capture_output=True, text=True, check=True).stdout
            self.assertIn("<h1>new work</h1>", content)
            env = dict(os.environ, GIT_AUTHOR_NAME="Harness",
                       GIT_AUTHOR_EMAIL="harness@localhost",
                       GIT_COMMITTER_NAME="Harness", GIT_COMMITTER_EMAIL="harness@localhost")
            subprocess.run(["git", "-C", str(root), "branch", "recovery-branch", lost],
                           check=True)
            subprocess.run(["git", "-C", str(root), "checkout", "master"], check=True)
            subprocess.run(["git", "-C", str(root), "merge", "--ff-only",
                            "recovery-branch"], check=True)
            deadline = time.monotonic() + 30
            self.assertEqual(harness.recovery_state(root, lost, deadline, env),
                             (True, True, True))


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
