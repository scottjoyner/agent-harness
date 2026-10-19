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

    def test_command_code_requires_acp(self):
        result = inspect(ROSTER, which=lambda b: None if b == "cmd-acp" else "/fake/bin")
        self.assertTrue(any("cmdcode" in x for x in result["missing_cli"]))

    def test_order_change_is_visible(self):
        config = copy.deepcopy(ROSTER)
        config["coders"].reverse()
        self.assertFalse(inspect(config, which=lambda _: "/fake/bin")["configuration_valid"])


if __name__ == "__main__":
    unittest.main()
