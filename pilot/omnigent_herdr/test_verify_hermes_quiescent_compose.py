"""Strict research-only no-dispatch Compose schema tests; never starts Docker."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent / "verify_hermes_quiescent_compose.py"
SPEC = importlib.util.spec_from_file_location("verify_hermes_quiescent_compose", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
IMAGE = "og-hermes-quiescent-base:abc123def456"


def sample():
    return {
        "name": "temporary-research-project",
        "services": {
            gate.SERVICE: {
                "profiles": [gate.PROFILE],
                "image": IMAGE,
                "pull_policy": "never",
                "entrypoint": ["/bin/sleep"],
                "command": ["75"],
                "restart": "no",
                "user": "1000:1000",
                "read_only": True,
                "network_mode": "none",
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "pids_limit": 24,
                "mem_limit": "134217728",
                "cpus": 0.5,
            }
        },
    }


class QuiescentComposeSafetyTests(unittest.TestCase):
    def check(self, doc=Ellipsis, expected=IMAGE):
        return gate.check(sample() if doc is Ellipsis else doc, expected)

    def deny(self, doc, code=None, expected=IMAGE):
        result = self.check(doc, expected)
        self.assertFalse(result["synthetic_compose_exact_policy_pass"], result)
        self.assertFalse(result["production_rollback_authorized"])
        self.assertFalse(result["production_deployment_authorized"])
        self.assertFalse(result["worker_task_dispatch_enabled"])
        self.assertEqual(result["decision"], "HOLD")
        if code:
            self.assertIn(code, result["violations"])

    def test_expected_synthetic_render_passes_but_cannot_authorize(self):
        result = self.check()
        self.assertTrue(result["synthetic_compose_exact_policy_pass"])
        self.assertEqual(result["violations"], [])
        self.assertFalse(result["production_rollback_authorized"])
        self.assertFalse(result["independent_claim_revocation_verified"])
        self.assertEqual(result["decision"], "HOLD")

    def test_default_and_malformed_are_denied(self):
        for doc in (None, False, {}, {"services": {}}, {"services": []}):
            with self.subTest(doc=str(doc)[:30]):
                self.deny(doc)

    def test_unexpected_second_service_denied(self):
        doc = sample()
        doc["services"]["assistx-hermes-adapter"] = {"image": IMAGE}
        self.deny(doc, "unexpected_services")

    def test_runtime_agent_dispatch_command_denied(self):
        for key, value in (
            ("entrypoint", ["/entrypoint.sh"]),
            ("command", ["python", "-m", "assistx.strict_executor_main"]),
            ("command", ["-c", "curl example.com"]),
        ):
            with self.subTest(key=key):
                doc = sample()
                doc["services"][gate.SERVICE][key] = value
                self.deny(doc, "invalid_" + key)

    def test_network_and_exposed_ports_denied(self):
        for key, value in (
            ("network_mode", "host"),
            ("ports", ["8000:8000"]),
            ("networks", {"default": {}}),
            ("extra_hosts", ["host.docker.internal:host-gateway"]),
            ("dns", ["1.1.1.1"]),
        ):
            with self.subTest(key=key):
                doc = sample()
                doc["services"][gate.SERVICE][key] = value
                self.deny(doc)

    def test_any_mount_or_privileged_namespace_denied(self):
        for key, value in (
            ("volumes", ["/nas:/nas:rw"]),
            ("volumes_from", ["assistx-hermes-adapter"]),
            ("devices", ["/dev/sda"]),
            ("privileged", True),
            ("pid", "host"),
            ("userns_mode", "host"),
            ("build", "."),
        ):
            with self.subTest(key=key):
                doc = sample()
                doc["services"][gate.SERVICE][key] = value
                self.deny(doc)

    def test_secrets_env_and_root_user_denied(self):
        for key, value in (
            ("env_file", ["production.env"]),
            ("environment", {"ASSISTX_URL": "http://api:8000"}),
            ("secrets", ["auth"]),
            ("user", "0:0"),
        ):
            with self.subTest(key=key):
                doc = sample()
                doc["services"][gate.SERVICE][key] = value
                self.deny(doc)

    def test_restart_policy_and_profile_are_fixed(self):
        for key, value in (
            ("restart", "unless-stopped"),
            ("restart", "always"),
            ("profiles", []),
            ("profiles", ["default"]),
            ("pull_policy", "always"),
        ):
            with self.subTest(key=key):
                doc = sample()
                doc["services"][gate.SERVICE][key] = value
                self.deny(doc)

    def test_privilege_and_immutable_rootfs_controls_enforced(self):
        for key, value in (
            ("read_only", False),
            ("read_only", 1),
            ("cap_drop", []),
            ("security_opt", []),
            ("security_opt", ["no-new-privileges:false"]),
        ):
            with self.subTest(key=key):
                doc = sample()
                doc["services"][gate.SERVICE][key] = value
                self.deny(doc)

    def test_resource_caps_must_match_normalized_compose(self):
        for key, value in (
            ("pids_limit", 100),
            ("mem_limit", 134217728),
            ("mem_limit", "9999999999"),
            ("cpus", 8.0),
        ):
            with self.subTest(key=key):
                doc = sample()
                doc["services"][gate.SERVICE][key] = value
                self.deny(doc)

    def test_image_name_and_reference_must_be_pinned_synthetic_label(self):
        for val in (
            "sha256:" + "a"*64,
            "docker.io/example/hermes:latest",
            "og-hermes-quiescent-base:latest",
            "../other",
            "og-hermes-quiescent-base:AAAAAAAAAAAAAAAA",
            "",
        ):
            with self.subTest(val=val[:20]):
                self.deny(sample(), expected=val)
        doc = sample()
        doc["services"][gate.SERVICE]["image"] = "some-other-local-image:test"
        self.deny(doc, code="untrusted_disposable_image_label")

    def test_top_level_extensions_denied(self):
        for key, value in (
            ("networks", {"default": {}}),
            ("volumes", {"data": {}}),
            ("secrets", {"key": {}}),
            ("x-release-authorized", True),
        ):
            doc = sample()
            doc[key] = value
            self.deny(doc, code="unknown_compose_root")

    def test_unit_test_is_pure_no_host_activity(self):
        with patch.object(gate, "main") as cmd:
            result = self.check()
        cmd.assert_not_called()
        self.assertTrue(result["synthetic_compose_exact_policy_pass"])

    def test_candidate_source_file_excludes_legacy_mounts(self):
        txt = Path(__file__).with_name("hermes_quiescent_fallback.compose.yaml").read_text()
        self.assertIn("profiles: [quiescent-canary]", txt)
        self.assertIn('entrypoint: ["/bin/sleep"]', txt)
        for x in ("env_file:", "volumes:", "ports:", "depends_on:",
                  "build:", "assistant.strict_executor", "assistx.strict_executor_main",
                  "unless-stopped", "ASSISTX_URL", "/media/scott", "/nas"):
            self.assertNotIn(x, txt)


if __name__ == "__main__":
    unittest.main()
