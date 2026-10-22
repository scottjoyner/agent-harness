import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from observer_remote_worker_fixture import run
from two_node_checkpoint_probe import exit_status, physical


class RemoteWorkerFixtureTests(unittest.TestCase):
    def test_protocol_sequence_only_with_synthetic_sqlite(self):
        with tempfile.TemporaryDirectory(prefix="og-protocol-unit-") as base:
            root = Path(base)
            cache = root / ".cache"
            cache.mkdir()
            original = list(cache.iterdir())
            commands = "".join(json.dumps({"op": op}) + "\n"
                               for op in ("initial", "redeem", "restore", "quit"))
            output = io.StringIO()
            with patch.object(Path, "home", return_value=root):
                run(stdin=io.StringIO(commands), stdout=output)
            replies = [json.loads(x) for x in output.getvalue().splitlines()]
            self.assertEqual([x["status"] for x in replies], [
                "initial", "redeemed", "restored_old_sqlite", "clean_exit",
            ])
            self.assertEqual(
                replies[0]["state_sha256"], replies[2]["state_sha256"])
            self.assertNotEqual(
                replies[0]["state_sha256"], replies[1]["state_sha256"])
            self.assertFalse(any("token" in x for x in replies))
            self.assertFalse(any("signing" in x for x in replies))
            self.assertEqual(list(cache.iterdir()), original)

    def test_remote_worker_rejects_wrong_command_order(self):
        with tempfile.TemporaryDirectory(prefix="og-protocol-bad-") as base:
            root = Path(base)
            (root / ".cache").mkdir()
            output = io.StringIO()
            with patch.object(Path, "home", return_value=root):
                run(stdin=io.StringIO('{"op":"redeem"}\n'), stdout=output)
            self.assertEqual([json.loads(x)["status"]
                              for x in output.getvalue().splitlines()], ["denied"])
            self.assertFalse(list((root / ".cache").iterdir()))


class ObserverTwoNodeTests(unittest.TestCase):
    def test_unapproved_worker_target_denied_without_ssh(self):
        report = physical(remote_host="unapproved-node")
        self.assertEqual(report["errors"], ["unapproved fixture worker target"])
        self.assertFalse(report["agent_dispatch_authorized"])
        self.assertEqual(report["provider_requests_forwarded"], 0)
        self.assertEqual(exit_status(report), 2)

    def test_admitted_fixture_is_never_production_authority(self):
        good = {
            "ephemeral_observer_key_held_separately": True,
            "newest_checkpoint_rejected_restored_db": True,
            "old_checkpoint_rejected_trusted_highwater": True,
            "old_checkpoint_with_old_floor_still_matches": True,
            "signing_key_on_worker": False,
            "worker_secret_on_observer": False,
            "independent_long_term_custody_verified": False,
            "continuous_publication_verified": False,
            "rollback_resistance_verified": False,
            "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0,
            "errors": [],
        }
        self.assertEqual(exit_status(good), 0)
        for key in ("independent_long_term_custody_verified",
                    "continuous_publication_verified",
                    "rollback_resistance_verified", "agent_dispatch_authorized",
                    "signing_key_on_worker", "worker_secret_on_observer"):
            with self.subTest(field=key):
                self.assertEqual(exit_status({**good, key: True}), 2)


if __name__ == "__main__":
    unittest.main()
