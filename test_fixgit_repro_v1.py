import contextlib
import io
import json
import tempfile
import time
import subprocess
import sys
import unittest
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

    def test_setup_timeout_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.json"
            def slow_setup(*args, **kwargs):
                return harness.run_process([sys.executable, "-c", "import time; time.sleep(30)"], args[1])
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
