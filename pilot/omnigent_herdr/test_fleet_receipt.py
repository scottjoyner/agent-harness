import json
import tempfile
import unittest
from pathlib import Path

from fleet_receipt import INPUTS, create_receipt, load_file


def complete_evidence():
    return {
        "preflight": {"configuration_valid": True, "bootstrap_ready": True,
                      "observed_host": "scott-Lenovo-ideapad-330S-15IKB"},
        "resource": {"resource_prefilter_passed": True},
        "sandbox": {"strict_isolation_passed": True},
        "catalog": {"requested_catalogs_listed": True},
        "apparmor": {"candidate_qualified": True, "profile_loaded": True},
    }


class ReceiptTests(unittest.TestCase):
    def test_all_green_does_not_authorize(self):
        r = create_receipt(complete_evidence(), timestamp="2026-10-19T12:00:00+00:00")
        self.assertTrue(r["machine_checks_pass"])
        self.assertFalse(r["agent_dispatch_authorized"])
        self.assertFalse(r["agent_egress_approved"])
        self.assertEqual(r["decision"], "HOLD")

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

    def test_oversized_input_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "evidence.json"
            path.write_text(json.dumps({"garbage": "x" * 65536}))
            with self.assertRaises(ValueError):
                load_file(path)


if __name__ == "__main__":
    unittest.main()
