import subprocess
import unittest
from pathlib import Path

from sandbox_probe import inspect


class SandboxProbeTests(unittest.TestCase):
    def test_missing_runtime_denied(self):
        report = inspect(which=lambda _: None)
        self.assertFalse(report["sandbox_verified"])
        self.assertFalse(report["agent_dispatch_authorized"])

    def test_failed_isolation_denied(self):
        report = inspect(which=lambda _: "/usr/bin/bwrap",
                         runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "denied"))
        self.assertFalse(report["sandbox_verified"])
        self.assertEqual(report["error"], "denied")

    def test_missing_negative_witness_denied(self):
        def simulate(cmd, **kw):
            Path(cmd[-2], "proof").write_text("sandboxed")
            return subprocess.CompletedProcess(cmd, 0, "WORKSPACE_OK\n", "")
        report = inspect(which=lambda _: "/usr/bin/bwrap", runner=simulate)
        self.assertFalse(report["sandbox_verified"])

    def test_positive_synthetic_witness(self):
        def simulate(cmd, **kw):
            Path(cmd[-2], "proof").write_text("sandboxed")
            return subprocess.CompletedProcess(
                cmd, 0, "WORKSPACE_OK\nOUTSIDE_DENIED\nNETWORK_DENIED\n", "")
        report = inspect(which=lambda _: "/usr/bin/bwrap", runner=simulate)
        self.assertTrue(report["sandbox_verified"])
        self.assertFalse(report["agent_dispatch_authorized"])


if __name__ == "__main__":
    unittest.main()
