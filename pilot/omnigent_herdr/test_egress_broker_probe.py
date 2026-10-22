import json
import unittest
from pathlib import Path

from egress_broker_probe import decide, sandbox_args, physical, exit_status


class EgressContractTests(unittest.TestCase):
    def test_failed_cli_exit_code(self):
        self.assertEqual(exit_status({}), 2)
        self.assertEqual(exit_status({"synthetic_socket_verified": True}), 0)

    def test_canonical_probe_is_never_dispatch(self):
        r = decide({"schema": 1, "op": "probe", "challenge": "n"},
                   challenge="n", peer_uid=1000, expected_uid=1000)
        self.assertEqual(r["status"], "synthetic_probe_only")
        self.assertFalse(r["dispatch_authorized"])

    def test_fail_closed_variations(self):
        attempts = [
            {"schema": 1, "op": "dispatch", "provider": "paid"},
            {"schema": 1, "op": "probe", "challenge": "wrong"},
            {"schema": True, "op": "probe", "challenge": "n"},
            {"schema": 1, "op": "probe", "challenge": "n", "shell": "ls"},
            "not an object", None, ["probe"],
        ]
        for request in attempts:
            with self.subTest(request=request):
                r = decide(request, challenge="n", peer_uid=1000,
                           expected_uid=1000)
                self.assertEqual(r["status"], "denied")
                self.assertFalse(r["dispatch_authorized"])

    def test_wrong_peer_uid_denied(self):
        r = decide({"schema": 1, "op": "probe", "challenge": "n"},
                   challenge="n", peer_uid=0, expected_uid=1000)
        self.assertEqual(r["status"], "denied")

    def test_bwrap_has_only_workspace_socket_and_usr_mounts(self):
        r = sandbox_args("bwrap", Path("/tmp/w"), Path("/tmp/s"),
                         Path("/tmp/no-access"), "n")
        self.assertIn("--unshare-net", r)
        self.assertIn("--clearenv", r)
        self.assertIn("--cap-drop", r)
        self.assertIn("--new-session", r)
        self.assertEqual([r[i+2] for i,x in enumerate(r[:-2])
                          if x == "--ro-bind"], ["/usr", "/run/fleet-broker"])
        self.assertEqual([r[i+2] for i,x in enumerate(r[:-2])
                          if x == "--bind"], ["/workspace"])

    def test_missing_sandbox_denied(self):
        r = physical(which=lambda _: None)
        self.assertFalse(r["synthetic_socket_verified"])
        self.assertFalse(r["agent_dispatch_authorized"])
        self.assertFalse(r["model_egress_authorized"])


if __name__ == "__main__":
    unittest.main()
