import json
import tempfile
import unittest
from pathlib import Path

from fleet_receipt import INPUTS, create_receipt, load_file, load_snapshot


def complete_evidence():
    values = {
        "preflight": {"configuration_valid": True, "bootstrap_ready": True,
                      "observed_host": "scott-Lenovo-ideapad-330S-15IKB"},
        "resource": {"resource_prefilter_passed": True},
        "sandbox": {"strict_isolation_passed": True},
        "catalog": {"requested_catalogs_listed": True},
        "apparmor": {"candidate_qualified": True, "profile_loaded": True},
        "egress": {
            "synthetic_socket_verified": True, "broker_peer_uid_verified": True,
            "sandbox_direct_network_denied": True,
            "sandbox_host_secret_unreadable": True,
            "mode": "deny_only_unix_socket_fixture",
            "provider_requests_forwarded": 0, "model_egress_authorized": False,
            "agent_dispatch_authorized": False,
        },
        "git": {
            "isolated_commit_verified": True, "seed_unchanged": True,
            "clone_clean": True, "outside_guard_unchanged": True,
            "direct_network_denied": True, "remote_push_attempted": False,
            "used_real_repository": False, "agent_dispatch_authorized": False,
        },
        "integrated": {
            "integrated_synthetic_passed": True,
            "broker_denial_verified": True, "isolated_git_verified": True,
            "host_state_unchanged": True, "direct_network_denied": True,
            "sandbox_capabilities_dropped": True,
            "sandbox_privilege_escalation_denied": True,
            "mode": "deny_only_integrated_synthetic_canary",
            "provider_requests_forwarded": 0,
            "remote_push_attempted": False,
            "used_real_repository": False,
            "agent_dispatch_authorized": False,
        },
    }
    for item in values.values():
        item["observed_host"] = "scott-Lenovo-ideapad-330S-15IKB"
    return values


class ReceiptTests(unittest.TestCase):
    def test_all_green_does_not_authorize(self):
        r = create_receipt(complete_evidence(), timestamp="2026-10-19T12:00:00+00:00")
        self.assertTrue(r["machine_checks_pass"])
        self.assertFalse(r["agent_dispatch_authorized"])
        self.assertFalse(r["agent_egress_approved"])
        self.assertEqual(r["decision"], "HOLD")

    def test_model_egress_authorization_cannot_be_forged(self):
        e = complete_evidence()
        e["egress"]["model_egress_authorized"] = True
        r = create_receipt(e, timestamp="test")
        self.assertFalse(r["gate_outcomes"]["egress"])
        self.assertFalse(r["agent_dispatch_authorized"])

    def test_attempted_remote_push_is_rejected(self):
        e = complete_evidence()
        e["git"]["remote_push_attempted"] = True
        r = create_receipt(e, timestamp="test")
        self.assertFalse(r["gate_outcomes"]["git"])

    def test_wrong_egress_mode_rejected(self):
        e = complete_evidence()
        e["egress"]["mode"] = "provider_forwarding"
        self.assertFalse(create_receipt(e, timestamp="test")["gate_outcomes"]["egress"])

    def test_destroyer_evidence_cannot_complete_lenovo_gate(self):
        e = complete_evidence()
        e["integrated"]["observed_host"] = "destroyer"
        r = create_receipt(e, timestamp="test")
        self.assertFalse(r["gate_outcomes"]["integrated"])
        self.assertFalse(r["machine_checks_pass"])
        self.assertFalse(r["agent_dispatch_authorized"])

    def test_no_host_label_is_denied(self):
        e = complete_evidence()
        e["sandbox"].pop("observed_host")
        self.assertFalse(create_receipt(e, timestamp="test")["gate_outcomes"]["sandbox"])

    def test_integrated_canary_missing_rejected(self):
        e = complete_evidence()
        del e["integrated"]
        r = create_receipt(e, timestamp="test")
        self.assertFalse(r["gate_outcomes"]["integrated"])

    def test_integrated_canary_with_real_model_forwarding_denied(self):
        e = complete_evidence()
        e["integrated"]["provider_requests_forwarded"] = 1
        self.assertFalse(create_receipt(e, timestamp="test")["gate_outcomes"]["integrated"])

    def test_integrated_canary_cannot_claim_push(self):
        e = complete_evidence()
        e["integrated"]["remote_push_attempted"] = True
        self.assertFalse(create_receipt(e, timestamp="test")["gate_outcomes"]["integrated"])

    def test_vendor_profile_not_loaded_fails(self):
        e = complete_evidence()
        e["apparmor"]["profile_loaded"] = False
        r = create_receipt(e, timestamp="test")
        self.assertFalse(r["gate_outcomes"]["apparmor"])

    def test_wrong_machine_fails_even_if_other_gates_pass(self):
        e = complete_evidence()
        e["preflight"]["observed_host"] = "destroyer"
        r = create_receipt(e, timestamp="test")
        self.assertFalse(r["gate_outcomes"]["preflight"])

    def test_missing_evidence_fails(self):
        r = create_receipt({}, timestamp="test")
        self.assertEqual(len(r["gate_outcomes"]), len(INPUTS))
        self.assertFalse(r["machine_checks_pass"])

    def test_string_truthy_is_denied(self):
        e = complete_evidence()
        e["sandbox"]["strict_isolation_passed"] = "true"
        r = create_receipt(e, timestamp="test")
        self.assertFalse(r["gate_outcomes"]["sandbox"])

    def test_unrelated_forged_authorization_ignored(self):
        e = complete_evidence()
        for item in e.values():
            item["agent_dispatch_authorized"] = True
            item["credentials_verified"] = True
        r = create_receipt(e, timestamp="test")
        self.assertFalse(r["agent_dispatch_authorized"])
        self.assertFalse(r["credentials_verified"])

    def test_non_file_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "evidence.json"
            p.write_text(json.dumps({"a": True}))
            self.assertEqual(load_file(p)["a"], True)
            link = root / "link"
            link.symlink_to(p)
            with self.assertRaises(ValueError):
                load_file(link)
            with self.assertRaises(ValueError):
                load_file(root)

    def test_digest_belongs_to_exact_parsed_bytes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "evidence.json"
            raw = b'{"sandbox_verified":false,"agent_dispatch_authorized":false}'
            path.write_bytes(raw)
            data, digest = load_snapshot(path)
            self.assertIs(data["sandbox_verified"], False)
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_symlink_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "evidence.json"
            p.write_text('{"schema":1}')
            q = root / "alias.json"
            q.symlink_to(p)
            with self.assertRaises(ValueError):
                load_snapshot(q)

    def test_oversized_input_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "evidence.json"
            path.write_text(json.dumps({"garbage": "x" * 65536}))
            with self.assertRaises(ValueError):
                load_file(path)


if __name__ == "__main__":
    unittest.main()
