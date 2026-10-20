"""All physical publication research reports remain non-authorizing."""
from __future__ import annotations

import unittest

from two_node_v2_outbox_probe import exit_status, physical


ALL = (
    "success", "missing_ack", "tampered_publication",
    "tampered_ack", "tampered_delivery",
)
UNPROVEN = (
    "worker_signer_independently_pinned",
    "observer_signer_independently_custodied",
    "external_highwater_verified", "source_attestation_verified",
    "publication_durability_verified",
    "agent_dispatch_authorized", "model_egress_authorized",
    "used_real_repository",
)
OUTCOMES = (
    "observer_signed_ack_verified_by_worker",
    "worker_signed_delivery_verified_by_observer",
    "tampered_publication_rejected_before_ack",
    "missing_ack_did_not_confirm_delivery",
    "tampered_ack_denied_by_worker",
    "tampered_delivery_rejected_by_observer",
    "uncertain_operation_stays_fenced",
)


def sample(scenario="success"):
    results = {name: False for name in OUTCOMES}
    if scenario == "success":
        results["observer_signed_ack_verified_by_worker"] = True
        results["worker_signed_delivery_verified_by_observer"] = True
    elif scenario == "missing_ack":
        results["missing_ack_did_not_confirm_delivery"] = True
        results["uncertain_operation_stays_fenced"] = True
    elif scenario == "tampered_publication":
        results["tampered_publication_rejected_before_ack"] = True
        results["uncertain_operation_stays_fenced"] = True
    elif scenario == "tampered_ack":
        results["tampered_ack_denied_by_worker"] = True
        results["uncertain_operation_stays_fenced"] = True
    elif scenario == "tampered_delivery":
        results["observer_signed_ack_verified_by_worker"] = True
        results["tampered_delivery_rejected_by_observer"] = True
        results["uncertain_operation_stays_fenced"] = True
    return {
        "schema": 1, "mode": "two_node_synthetic_v2_outbox_publication",
        "scenario": scenario,
        "durable_inbox_mode": False,
        "durable_inbox_state_verified": False,
        "durable_inbox_fence_survives_reopen": False,
        "durable_inbox_completed_non_authorizing": False,
        "durable_inbox_independent_custody_verified": False,
        "durable_inbox_rollback_resistance_verified": False,
        "worker_and_observer_keys_distinct": True,
        "worker_signed_outbox_publication_verified": True,
        "remote_exited_cleanly": True,
        **results,
        **{name: False for name in UNPROVEN},
        "provider_requests_forwarded": 0, "errors": [],
    }


class V2TwoNodeSafetyContract(unittest.TestCase):
    def test_five_scenarios_are_no_dispatch_even_on_success(self):
        for scenario in ALL:
            with self.subTest(scenario=scenario):
                event = sample(scenario)
                self.assertEqual(exit_status(event), 0)
                self.assertFalse(event["agent_dispatch_authorized"])

    def test_durable_success_and_negative_report_contracts(self):
        for scenario in ALL:
            with self.subTest(scenario=scenario):
                event = sample(scenario)
                event["durable_inbox_mode"] = True
                event["durable_inbox_state_verified"] = True
                event["durable_inbox_fence_survives_reopen"] = True
                event["durable_inbox_completed_non_authorizing"] = (
                    scenario == "success")
                self.assertEqual(exit_status(event), 0)
                self.assertFalse(event["agent_dispatch_authorized"])

    def test_missing_durable_fence_or_forged_custody_denied(self):
        event = sample("missing_ack")
        event.update({
            "durable_inbox_mode": True,
            "durable_inbox_state_verified": True,
            "durable_inbox_fence_survives_reopen": False,
        })
        self.assertEqual(exit_status(event), 2)
        event["durable_inbox_fence_survives_reopen"] = True
        self.assertEqual(exit_status(event), 0)
        event["durable_inbox_independent_custody_verified"] = True
        self.assertEqual(exit_status(event), 2)

    def test_missing_or_incomplete_qualification_rejected(self):
        self.assertEqual(exit_status({}), 2)
        for key in (
            "worker_and_observer_keys_distinct",
            "worker_signed_outbox_publication_verified",
            "remote_exited_cleanly",
        ):
            with self.subTest(key=key):
                self.assertEqual(exit_status({
                    **sample(), key: False,
                }), 2)

    def test_forged_key_custody_or_live_dispatch_claim_rejected(self):
        for key in UNPROVEN:
            with self.subTest(key=key):
                self.assertEqual(exit_status({
                    **sample(), key: True,
                }), 2)

    def test_missing_verified_worker_delivery_cannot_pass_success(self):
        self.assertEqual(exit_status({
            **sample(), "worker_signed_delivery_verified_by_observer": False,
        }), 2)

    def test_missing_fence_in_negative_scenarios_rejected(self):
        for scenario in ALL[1:]:
            with self.subTest(scenario=scenario):
                self.assertEqual(exit_status({
                    **sample(scenario),
                    "uncertain_operation_stays_fenced": False,
                }), 2)

    def test_unapproved_target_is_rejected_before_ssh(self):
        report = physical(remote="production-host")
        self.assertNotEqual(exit_status(report), 0)
        self.assertEqual(report["errors"], ["unapproved scenario/remote worker"])

    def test_invalid_scenario_is_rejected_before_ssh(self):
        report = physical(scenario="send_models")
        self.assertNotEqual(exit_status(report), 0)

    def test_error_or_provider_forwarding_rejected(self):
        self.assertEqual(exit_status({
            **sample(), "errors": ["unexpected"],
        }), 2)
        self.assertEqual(exit_status({
            **sample(), "provider_requests_forwarded": 1,
        }), 2)


if __name__ == "__main__":
    unittest.main()
