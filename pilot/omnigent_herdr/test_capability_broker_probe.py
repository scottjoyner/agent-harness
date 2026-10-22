import unittest
from pathlib import Path

from capability_broker_probe import command, exit_status, physical


class PhysicalLeaseProbeTests(unittest.TestCase):
    def test_absent_bwrap_denied(self):
        r = physical(which=lambda _: None)
        self.assertFalse(r["physical_lease_checks_passed"])
        self.assertFalse(r["agent_dispatch_authorized"])
        self.assertEqual(r["provider_requests_forwarded"], 0)

    def test_cli_exit_fails_closed(self):
        self.assertEqual(exit_status({}), 2)
        self.assertEqual(exit_status({"physical_lease_checks_passed": True}), 0)

    def test_argv_allows_only_fixture_workspace_and_socket(self):
        argv = command("bwrap", Path("/fixture/work"), Path("/fixture/socket"),
                       Path("/fixture/private/key"))
        self.assertIn("--unshare-net", argv)
        self.assertIn("--clearenv", argv)
        self.assertIn("--cap-drop", argv)
        mounts = [(argv[i], argv[i+1], argv[i+2]) for i in range(len(argv)-2)
                  if argv[i] in ("--bind", "--ro-bind")]
        self.assertEqual(mounts, [
            ("--ro-bind", "/usr", "/usr"),
            ("--bind", "/fixture/work", "/workspace"),
            ("--ro-bind", "/fixture/socket", "/run/fleet-broker"),
        ])
        self.assertNotIn("/fixture/private/key",
                         [src for _, src, _ in mounts])
        self.assertNotIn("/home", [dst for _, _, dst in mounts])


if __name__ == "__main__":
    unittest.main()
