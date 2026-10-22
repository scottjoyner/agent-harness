"""Research rollback test suite: deny restoration of unsafe legacy Hermes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent / "verify_hermes_quiescent_rollback.py"
SPEC = importlib.util.spec_from_file_location("verify_hermes_quiescent_rollback", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def safe_scratch():
    return {
        "schema": 1, "mode": "disposable_no_dispatch", "candidate_absent": True,
        "restart_policy": "no", "worker_containers": 0, "synthetic_claims": 0,
        "provider_requests": 0, "legacy_restore_requested": False,
        "legacy_mounts_restore_requested": False, "private_scratch_retained": True,
    }


class QuiescentRollbackTests(unittest.TestCase):
    def test_disposable_success_does_not_authorize_production(self):
        r = gate.synthetic_quiescence(safe_scratch())
        self.assertTrue(r["disposable_quiescence_observed"])
        self.assertFalse(r["production_rollback_authorized"])
        self.assertFalse(r["independent_claim_revocation_verified"])
        self.assertFalse(r["no_dispatch_production_transition_verified"])
        self.assertEqual(r["decision"], "HOLD")

    def test_invalid_records_denied(self):
        for record in (None, True, {}, {"schema": 1},
                       {**safe_scratch(), "production_rollback_authorized": True}):
            with self.subTest(record=str(record)[:28]):
                self.assertFalse(gate.synthetic_quiescence(record)["disposable_quiescence_observed"])

    def test_legacy_image_restoration_is_denied(self):
        for key in ("legacy_restore_requested", "legacy_mounts_restore_requested"):
            s = safe_scratch()
            s[key] = True
            self.assertIn("unsafe_legacy_restoration", gate.synthetic_quiescence(s)["violations"])

    def test_worker_auto_restart_is_denied(self):
        s = safe_scratch()
        s["restart_policy"] = "unless-stopped"
        self.assertIn("restarting_worker_forbidden", gate.synthetic_quiescence(s)["violations"])

    def test_worker_or_claim_still_running_denied(self):
        for field in ("worker_containers", "synthetic_claims", "provider_requests"):
            with self.subTest(field=field):
                s = safe_scratch()
                s[field] = 1
                self.assertFalse(gate.synthetic_quiescence(s)["disposable_quiescence_observed"])
                s[field] = True
                self.assertFalse(gate.synthetic_quiescence(s)["disposable_quiescence_observed"])

    def test_nonexistent_worker_or_lost_state_not_quiescent(self):
        for field in ("candidate_absent", "private_scratch_retained"):
            s = safe_scratch()
            s[field] = False
            self.assertFalse(gate.synthetic_quiescence(s)["disposable_quiescence_observed"])

    def test_default_never_does_docker_or_reads_compose(self):
        with patch.object(gate.subprocess, "run") as shell:
            with patch.object(Path, "read_text") as read:
                r = gate.default_hold()
        shell.assert_not_called()
        read.assert_not_called()
        self.assertEqual(r["decision"], "HOLD")

    def test_live_legacy_unsafe_ancestor_refused(self):
        with tempfile.TemporaryDirectory(prefix="og-hermes-fallback-") as folder:
            root = Path(folder)
            protected = root / "ssd" / "knowledge" / "watchdog"
            protected.mkdir(parents=True)
            alias = root / "legacy"
            alias.symlink_to(protected.parent, target_is_directory=True)
            mounts = [
                {"Type": "bind", "Source": str(root / "ssd"), "RW": True},
                {"Type": "bind", "Source": str(alias), "RW": True},
            ]
            snap = {
                "running": True, "image": gate.EXPECTED_IMAGE,
                "restart_policy": "unless-stopped", "mounts": mounts,
                "compose_build": True, "compose_readonly": False,
                "compose_caps_dropped": False, "compose_mount_count": 16,
            }
            with patch.object(gate, "EXPECTED_PROTECTED", protected):
                out = gate.assess_live_snapshot(snap)
            self.assertEqual(out["protected_overlap_count"], 2)
            self.assertEqual(out["live_writable_mounts"], 2)
            self.assertIn("unsafe_legacy_image_is_rollback_target", out["violations"])
            self.assertIn("legacy_auto_restart_enabled", out["violations"])
            self.assertIn("protected_evidence_overlap", out["violations"])
            self.assertFalse(out["safe_legacy_fallback"])

    def test_unknown_mount_source_does_not_pass(self):
        with tempfile.TemporaryDirectory(prefix="og-hermes-fallback-") as folder:
            protected = Path(folder) / "protected"
            protected.mkdir()
            snap = {
                "running": False, "image": "sha256:" + "f"*64,
                "restart_policy": "no",
                "mounts": [{"Type": "bind", "Source": str(protected / "notmounted"), "RW": False}],
                "compose_build": False, "compose_readonly": True,
                "compose_caps_dropped": True, "compose_mount_count": 2,
            }
            with patch.object(gate, "EXPECTED_PROTECTED", protected):
                out = gate.assess_live_snapshot(snap)
            self.assertEqual(out["unknown_source_count"], 1)
            self.assertIn("mount_source_identity_unknown", out["violations"])

    def test_clean_simulated_runtime_never_confers_prod_release(self):
        with tempfile.TemporaryDirectory(prefix="og-hermes-fallback-") as folder:
            root = Path(folder)
            p = root / "protected"
            p.mkdir()
            approved = root / "approved"
            approved.mkdir()
            ws = root / "workspace"
            ws.mkdir()
            snap = {
                "running": False, "image": "sha256:" + "a"*64,
                "restart_policy": "no",
                "mounts": [{"Type":"bind", "Source":str(approved), "RW":False},
                           {"Type":"bind", "Source":str(ws), "RW":True}],
                "compose_build": False, "compose_readonly": True,
                "compose_caps_dropped": True, "compose_mount_count": 2,
            }
            with patch.object(gate, "EXPECTED_PROTECTED", p):
                out = gate.assess_live_snapshot(snap)
            self.assertEqual(out["violations"], [])
            self.assertFalse(out["production_rollback_authorized"])
            self.assertEqual(out["decision"], "HOLD")

    def test_malformed_snapshot_fails_closed(self):
        for sample in (None, {}, {"mounts": []}, {"unexpected": True}):
            self.assertIn("live_metadata_schema", gate.assess_live_snapshot(sample)["violations"])

    def test_report_contains_no_secret_or_host_sources(self):
        out = gate.synthetic_quiescence(safe_scratch())
        rendered = str(out)
        for value in ("/home/scott", "/media/scott", "FLEET_ROUTER_BEARER_TOKEN"):
            self.assertNotIn(value, rendered)


if __name__ == "__main__":
    unittest.main()
