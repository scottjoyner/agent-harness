"""Deterministic offline checks for the Herdr/Omnigent pilot gate."""
import copy
import json
import unittest
from pathlib import Path

from preflight import inspect as _inspect


def inspect(config, which):
    return _inspect(config, which=which, hostname="scott-Lenovo-ideapad-330S-15IKB")


ROSTER = json.loads(Path(__file__).with_name("roster.json").read_text())


class PilotChecks(unittest.TestCase):
    def test_installed_prerequisites_are_not_dispatch_proof(self):
        result = inspect(ROSTER, which=lambda _: "/fake/bin")
        self.assertTrue(result["bootstrap_ready"])
        self.assertFalse(result["dispatch_ready"])
        self.assertFalse(result["model_pins_verified"])

    def test_wrong_machine_denied(self):
        result = _inspect(ROSTER, which=lambda _: "/fake/bin", hostname="x1-370")
        self.assertFalse(result["bootstrap_ready"])
        self.assertTrue(any("Wrong execution host" in x for x in result["blockers"]))

    def test_missing_tmux_blocks_bootstrap(self):
        result = inspect(ROSTER, which=lambda b: None if b == "tmux" else "/fake/bin")
        self.assertFalse(result["bootstrap_ready"])
        self.assertTrue(any("tmux" in x for x in result["missing_cli"]))

    def test_public_tunnel_denied(self):
        config = copy.deepcopy(ROSTER)
        config["policy"]["public_tunnel"] = True
        self.assertFalse(inspect(config, which=lambda _: "/fake/bin")["configuration_valid"])

    def test_default_missing_deny_is_rejected(self):
        config = copy.deepcopy(ROSTER)
        del config["policy"]["production_writes"]
        self.assertFalse(inspect(config, which=lambda _: "/fake/bin")["configuration_valid"])

    def test_parallelism_is_bounded(self):
        config = copy.deepcopy(ROSTER)
        config["policy"]["max_parallel_coders"] = 4
        self.assertFalse(inspect(config, which=lambda _: "/fake/bin")["bootstrap_ready"])

    def test_bad_role_or_missing_pin_denied(self):
        config = copy.deepcopy(ROSTER)
        config["coders"][1]["model_pin"] = ""
        self.assertFalse(inspect(config, which=lambda _: "/fake/bin")["configuration_valid"])
        config = copy.deepcopy(ROSTER)
        config["reviewer"]["id"] = "claude"
        self.assertFalse(inspect(config, which=lambda _: "/fake/bin")["configuration_valid"])

    def test_free_tier_pin_cannot_be_silently_replaced(self):
        config = copy.deepcopy(ROSTER)
        config["coders"][0]["model_pin"] = "kilo/kilo-auto/frontier"
        result = inspect(config, which=lambda _: "/fake/bin")
        self.assertFalse(result["configuration_valid"])
        self.assertTrue(any("Unreviewed model pin for kilo" in x for x in result["blockers"]))

    def test_reviewer_pin_must_remain_requested(self):
        config = copy.deepcopy(ROSTER)
        config["reviewer"]["model_pin"] = "gpt-5.5-codex"
        self.assertFalse(inspect(config, which=lambda _: "/fake/bin")["configuration_valid"])

    def test_source_commit_change_requires_review(self):
        config = copy.deepcopy(ROSTER)
        config["source"]["evaluated_commit"] = "0" * 40
        self.assertFalse(inspect(config, which=lambda _: "/fake/bin")["configuration_valid"])

    def test_orchestrator_requested_model_change_requires_review(self):
        config = copy.deepcopy(ROSTER)
        config["orchestrator"]["requested_model"] = "Opus 5"
        self.assertFalse(inspect(config, which=lambda _: "/fake/bin")["configuration_valid"])

    def test_blink_alone_does_not_qualify_freebuff(self):
        result = inspect(ROSTER, which=lambda b: None if b == "freebuff" else "/fake/bin",
                         )
        self.assertFalse(result["bootstrap_ready"])
        self.assertTrue(any(x.startswith("freebuff:") for x in result["missing_cli"]))

    def test_native_freebuff_alone_does_not_qualify_bridge(self):
        result = inspect(ROSTER, which=lambda b: None if b == "blink" else "/fake/bin",
                         )
        self.assertFalse(result["bootstrap_ready"])
        self.assertTrue(any(x.startswith("freebuff:") for x in result["missing_cli"]))

    def test_missing_node_runtime_denied(self):
        result = inspect(ROSTER, which=lambda b: None if b == "node" else "/fake/bin",
                         )
        self.assertFalse(result["bootstrap_ready"])
        self.assertTrue(any(x.startswith("node:") for x in result["missing_cli"]))

    def test_command_code_requires_acp(self):
        result = inspect(ROSTER, which=lambda b: None if b == "cmd-acp" else "/fake/bin")
        self.assertTrue(any("cmdcode" in x for x in result["missing_cli"]))

    def test_order_change_is_visible(self):
        config = copy.deepcopy(ROSTER)
        config["coders"].reverse()
        self.assertFalse(inspect(config, which=lambda _: "/fake/bin")["configuration_valid"])


if __name__ == "__main__":
    unittest.main()
