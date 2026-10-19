"""Offline failure tests for the x1/Lenovo synthetic observer ACK path."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ack_remote_worker_fixture import run
from two_node_ack_probe import exit_status, physical


def fixture_claims(scenario="success"):
    return {
        "schema": 1,
        "observed_host": "x1-370",
        "remote_worker": "scott-Lenovo-ideapad-330S-15IKB",
        "mode": "two_node_synthetic_observer_ack_fixture",
        "scenario": scenario,
        "signed_prepare_verified": True,
        "worker_committed_synthetic_receipt": True,
        "observer_validated_postcommit_snapshot": True,
        "worker_key_distinct_from_observer": True,
        "worker_commit_receipt_verified": scenario != "tampered_worker_commit",
        "worker_delivery_receipt_verified": scenario == "success",
        "tampered_worker_receipt_denied": scenario in (
            "tampered_worker_commit", "tampered_worker_delivery"),
        "signed_checkpoint_verified": scenario != "tampered_worker_commit",
        "signed_ack_accepted": scenario == "success",
        "missing_or_tampered_ack_denied": scenario in (
            "missing_ack", "tampered_ack"),
        "observer_fenced_after_failed_delivery": scenario != "success",
        "clean_worker_exit": True,
        "observer_private_key_sent_to_worker": False,
        "worker_hmac_key_sent_to_observer": False,
        "worker_signing_key_sent_to_observer": False,
        "worker_trust_root_independently_pinned": False,
        "worker_receipt_durability_verified": False,
        "independent_key_custody_verified": False,
        "durable_publication_verified": False,
        "rollback_resistance_verified": False,
        "agent_dispatch_authorized": False,
        "provider_requests_forwarded": 0,
        "used_real_repository": False,
        "errors": [],
    }


class AckProtocolSafetyTests(unittest.TestCase):
    def test_five_scenarios_are_all_deny_only(self):
        for scenario in ("success", "missing_ack", "tampered_ack",
                         "tampered_worker_commit", "tampered_worker_delivery"):
            with self.subTest(scenario=scenario):
                sample = fixture_claims(scenario)
                self.assertEqual(exit_status(sample), 0)
                self.assertFalse(sample["agent_dispatch_authorized"])
                self.assertEqual(sample["provider_requests_forwarded"], 0)

    def test_inconsistent_ack_claims_rejected(self):
        good = fixture_claims("missing_ack")
        for key in (
            "observer_fenced_after_failed_delivery",
            "missing_or_tampered_ack_denied",
            "worker_committed_synthetic_receipt",
            "signed_checkpoint_verified", "clean_worker_exit",
            "worker_commit_receipt_verified",
            "worker_key_distinct_from_observer",
        ):
            with self.subTest(key=key):
                self.assertEqual(exit_status({**good, key: False}), 2)

    def test_missing_worker_delivery_signature_cannot_report_success(self):
        good = fixture_claims("success")
        self.assertEqual(exit_status({
            **good, "worker_delivery_receipt_verified": False}), 2)

    def test_tampered_commit_leaves_observer_unresolved(self):
        good = fixture_claims("tampered_worker_commit")
        self.assertEqual(exit_status(good), 0)
        self.assertEqual(exit_status({
            **good, "observer_fenced_after_failed_delivery": False}), 2)

    def test_forged_production_authorization_rejected(self):
        good = fixture_claims()
        for key in (
            "observer_private_key_sent_to_worker",
            "worker_hmac_key_sent_to_observer",
            "worker_signing_key_sent_to_observer",
            "worker_trust_root_independently_pinned",
            "worker_receipt_durability_verified",
            "independent_key_custody_verified",
            "durable_publication_verified",
            "rollback_resistance_verified",
            "agent_dispatch_authorized",
            "used_real_repository",
        ):
            with self.subTest(key=key):
                self.assertEqual(exit_status({**good, key: True}), 2)

    def test_unapproved_target_fails_before_ssh(self):
        result = physical(remote_host="production-node")
        self.assertNotEqual(exit_status(result), 0)
        self.assertEqual(result["errors"], ["target or scenario not authorized"])

    def test_unapproved_scenario_fails_before_ssh(self):
        result = physical(scenario="open_proxy")
        self.assertNotEqual(exit_status(result), 0)
        self.assertFalse(result["signed_ack_accepted"])

    def test_remote_worker_refuses_unsolicited_ack_and_cleans_fixture(self):
        with tempfile.TemporaryDirectory(prefix="ack-worker-test-") as td:
            root = Path(td)
            (root / ".cache").mkdir()
            buffer = io.StringIO()
            data = io.StringIO(json.dumps({
                "op": "ack", "record": None, "checkpoint": None}) + "\n")
            with patch.object(Path, "home", return_value=root):
                run(source=data, sink=buffer)
            outputs = [json.loads(v) for v in buffer.getvalue().splitlines()]
            self.assertEqual([v["status"] for v in outputs], ["denied"])
            self.assertFalse(outputs[0]["agent_dispatch_authorized"])
            self.assertEqual(outputs[0]["provider_requests_forwarded"], 0)
            self.assertFalse(list((root / ".cache").iterdir()))

    def test_remote_worker_refuses_malformed_key_and_cleans_fixture(self):
        with tempfile.TemporaryDirectory(prefix="ack-worker-test-") as td:
            root = Path(td)
            (root / ".cache").mkdir()
            buffer = io.StringIO()
            packets = [
                {"op": "init"},
                {"op": "prepare", "record": {},
                 "public_key": "invalid??", "fingerprint": "0" * 64},
            ]
            with patch.object(Path, "home", return_value=root):
                run(source=io.StringIO("".join(
                    json.dumps(v) + "\n" for v in packets)), sink=buffer)
            outputs = [json.loads(v) for v in buffer.getvalue().splitlines()]
            self.assertEqual([v["status"] for v in outputs],
                             ["initial", "denied"])
            self.assertTrue(all(v["agent_dispatch_authorized"] is False
                                for v in outputs))
            self.assertFalse(list((root / ".cache").iterdir()))

    def test_remote_worker_init_exposes_no_worker_hmac_key(self):
        with tempfile.TemporaryDirectory(prefix="ack-worker-test-") as td:
            root = Path(td)
            (root / ".cache").mkdir()
            out = io.StringIO()
            with patch.object(Path, "home", return_value=root):
                run(source=io.StringIO('{"op":"init"}\n'), sink=out)
            first = json.loads(out.getvalue().splitlines()[0])
            self.assertEqual(first["status"], "initial")
            self.assertNotIn("worker_hmac_key", first)
            self.assertNotIn("token", first)
            self.assertNotIn("observer_private_key", first)
            self.assertEqual(first["provider_requests_forwarded"], 0)
            self.assertFalse(list((root / ".cache").iterdir()))


if __name__ == "__main__":
    unittest.main()
