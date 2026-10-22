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
        "lease": {
            "mode": "synthetic_hmac_lease_deny_only",
            "physical_lease_checks_passed": True, "replay_denied": True,
            "cross_owner_denied": True, "quota_denied": True,
            "revocation_denied": True, "epoch_denied": True,
            "hmac_forgery_denied": True, "scope_model_mismatch_denied": True,
            "forbidden_operations_denied": True,
            "broker_peer_uid_matched": True,
            "sandbox_direct_network_denied": True,
            "host_signing_key_unreadable": True,
            "host_key_unchanged": True,
            "provider_requests_forwarded": 0, "model_egress_authorized": False,
            "agent_dispatch_authorized": False, "used_real_repository": False,
        },
        "durability_fixture": {
            "mode": "synthetic_sqlite_restart_and_rollback_fixture",
            "restart_replay_denied": True, "restart_quota_enforced": True,
            "restart_revocation_denied": True, "restart_epoch_denied": True,
            "cross_process_concurrency_serialized": True,
            "db_missing_fail_closed": True, "key_not_persisted": True,
            "file_rollback_vulnerability_reproduced": True,
            "file_rollback_protection_verified": False,
            "independent_checkpoint_verified": False,
            "agent_dispatch_authorized": False,
            "provider_requests_forwarded": 0, "used_real_repository": False,
        },
        "observer_fixture": {
            "mode": "ephemeral_ed25519_observer_fixture",
            "signed_latest_state_matches": True,
            "restored_old_db_rejected_with_latest_checkpoint": True,
            "stale_checkpoint_rejected_with_trusted_floor": True,
            "old_checkpoint_with_old_floor_still_matches": True,
            "altered_signature_rejected": True,
            "observer_public_key_pin_enforced": True,
            "observer_private_key_persisted": False,
            "physical_observer_custody_verified": False,
            "continuous_publication_verified": False,
            "rollback_resistance_verified": False,
            "provider_requests_forwarded": 0,
            "agent_dispatch_authorized": False,
        },
        "observer_journal_fixture": {
            "mode": "synthetic_restart_durable_observer_journal",
            "prepare_fence_survived_restart": True,
            "unmodified_prepare_can_abort": True,
            "signed_unconfirmed_ack_survived_restart": True,
            "tampered_ack_cannot_release_fence": True,
            "correct_ack_can_confirm_after_restart": True,
            "postconfirm_allows_next_prepare": True,
            "restored_old_observer_db_vulnerability_reproduced": True,
            "signing_key_on_disk": False,
            "independent_signer_custody_verified": False,
            "external_highwater_verified": False,
            "agent_dispatch_authorized": False,
            "model_egress_authorized": False,
            "provider_requests_forwarded": 0,
            "used_real_repository": False,
            "errors": [],
        },
        "worker_receipt_fixture": {
            "mode": "synthetic_restart_worker_signed_receipt_journal",
            "unlogged_lease_debit_detected": True,
            "signed_commit_survives_worker_process_restart": True,
            "commit_pending_delivery_fences_second_commit": True,
            "matching_signed_delivery_survives_restart": True,
            "stale_anchor_replay_denied": True,
            "rolled_back_worker_state_mismatch_detected": True,
            "older_receipt_journal_denied_with_newer_anchor": True,
            "older_receipt_journal_with_older_anchor_still_valid": True,
            "wrong_worker_key_denied_on_restart": True,
            "independent_worker_key_custody_verified": False,
            "externally_durable_anchor_verified": False,
            "atomic_lease_receipt_commit_verified": False,
            "source_authority_verified": False,
            "agent_dispatch_authorized": False,
            "model_egress_authorized": False,
            "provider_requests_forwarded": 0,
            "used_real_repository": False,
            "errors": [],
        },
        "atomic_outbox_fixture": {
            "mode": "synthetic_single_sqlite_atomic_lease_outbox",
            "v2_legacy_schema_separation_verified": True,
            "pre_insert_failure_left_no_debit": True,
            "post_insert_failure_rolled_back_both": True,
            "committed_debit_and_authenticated_intent": True,
            "restart_outbox_intent_persisted": True,
            "duplicate_operation_rejected": True,
            "second_receipt_chained_in_one_db": True,
            "old_lease_image_rejected_with_newer_head": True,
            "old_image_with_old_head_still_valid": True,
            "signing_key_saved_to_disk": False,
            "independent_external_highwater_verified": False,
            "observer_publication_atomic_verified": False,
            "real_resource_authority_verified": False,
            "agent_dispatch_authorized": False,
            "model_egress_authorized": False,
            "provider_requests_forwarded": 0,
            "used_real_repository": False,
            "errors": [],
        },
        "v2_observer_inbox_fixture": {
            "mode": "synthetic_restart_durable_v2_observer_inbox",
            "worker_v2_atomic_outbox_bound": True,
            "publication_fence_survives_process_restart": True,
            "signed_ack_fence_survives_process_restart": True,
            "lost_ack_cannot_be_reissued": True,
            "forged_delivery_cannot_clear_fence": True,
            "valid_delivery_survives_process_restart": True,
            "stale_anchor_denied": True,
            "newer_anchor_detects_old_valid_db": True,
            "old_db_and_old_anchor_still_match": True,
            "unverified_publication_quarantine_survived_restart": True,
            "signing_keys_persisted": False,
            "independent_worker_identity_verified": False,
            "durable_external_anchor_verified": False,
            "source_transaction_attested": False,
            "rollback_resistance_verified": False,
            "agent_dispatch_authorized": False,
            "model_egress_authorized": False,
            "provider_requests_forwarded": 0,
            "used_real_repository": False,
            "errors": [],
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

    def test_durable_restart_evidence_missing_denied(self):
        e = complete_evidence()
        del e["durability_fixture"]
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["durability_fixture"])

    def test_durable_vulnerability_cannot_be_falsified_as_fixed(self):
        e = complete_evidence()
        e["durability_fixture"]["file_rollback_protection_verified"] = True
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["durability_fixture"])

    def test_observer_custody_claim_denied_without_real_attestation(self):
        for k in ("physical_observer_custody_verified",
                  "continuous_publication_verified",
                  "rollback_resistance_verified",
                  "agent_dispatch_authorized"):
            with self.subTest(key=k):
                e = complete_evidence()
                e["observer_fixture"][k] = True
                self.assertFalse(create_receipt(e, timestamp="test")[
                    "gate_outcomes"]["observer_fixture"])

    def test_observer_fixture_cross_host_denied(self):
        e = complete_evidence()
        e["observer_fixture"]["observed_host"] = "destroyer"
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["observer_fixture"])

    def test_observer_journal_missing_denied(self):
        e = complete_evidence()
        del e["observer_journal_fixture"]
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["observer_journal_fixture"])

    def test_observer_journal_cannot_claim_rollback_resistance(self):
        for field in ("external_highwater_verified",
                      "independent_signer_custody_verified",
                      "agent_dispatch_authorized"):
            with self.subTest(field=field):
                e = complete_evidence()
                e["observer_journal_fixture"][field] = True
                self.assertFalse(create_receipt(e, timestamp="test")[
                    "gate_outcomes"]["observer_journal_fixture"])

    def test_observer_journal_must_prove_rollback_attack(self):
        e = complete_evidence()
        e["observer_journal_fixture"][
            "restored_old_observer_db_vulnerability_reproduced"] = False
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["observer_journal_fixture"])

    def test_observer_journal_cross_host_denied(self):
        e = complete_evidence()
        e["observer_journal_fixture"]["observed_host"] = "destroyer"
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["observer_journal_fixture"])

    def test_worker_receipt_missing_denied(self):
        e = complete_evidence()
        del e["worker_receipt_fixture"]
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["worker_receipt_fixture"])

    def test_worker_receipt_vulnerability_must_be_visible(self):
        e = complete_evidence()
        e["worker_receipt_fixture"]["unlogged_lease_debit_detected"] = False
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["worker_receipt_fixture"])

    def test_worker_receipt_cannot_claim_durable_atomicity_or_authority(self):
        for field in (
            "independent_worker_key_custody_verified",
            "externally_durable_anchor_verified",
            "atomic_lease_receipt_commit_verified",
            "source_authority_verified",
            "agent_dispatch_authorized",
        ):
            with self.subTest(field=field):
                e = complete_evidence()
                e["worker_receipt_fixture"][field] = True
                self.assertFalse(create_receipt(e, timestamp="test")[
                    "gate_outcomes"]["worker_receipt_fixture"])

    def test_worker_receipt_mixed_host_denied(self):
        e = complete_evidence()
        e["worker_receipt_fixture"]["observed_host"] = "destroyer"
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["worker_receipt_fixture"])

    def test_atomic_outbox_missing_denied(self):
        e = complete_evidence()
        del e["atomic_outbox_fixture"]
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["atomic_outbox_fixture"])

    def test_atomic_outbox_fault_injection_required(self):
        e = complete_evidence()
        e["atomic_outbox_fixture"]["post_insert_failure_rolled_back_both"] = False
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["atomic_outbox_fixture"])

    def test_atomic_outbox_must_not_claim_external_authority(self):
        for name in (
            "independent_external_highwater_verified",
            "observer_publication_atomic_verified",
            "real_resource_authority_verified",
            "agent_dispatch_authorized",
        ):
            with self.subTest(name=name):
                e = complete_evidence()
                e["atomic_outbox_fixture"][name] = True
                self.assertFalse(create_receipt(e, timestamp="test")[
                    "gate_outcomes"]["atomic_outbox_fixture"])

    def test_atomic_outbox_mixed_host_rejected(self):
        e = complete_evidence()
        e["atomic_outbox_fixture"]["observed_host"] = "destroyer"
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["atomic_outbox_fixture"])

    def test_v2_observer_inbox_evidence_missing_denied(self):
        e = complete_evidence()
        del e["v2_observer_inbox_fixture"]
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["v2_observer_inbox_fixture"])

    def test_v2_observer_inbox_must_exhibit_rollback_vulnerability(self):
        e = complete_evidence()
        e["v2_observer_inbox_fixture"]["old_db_and_old_anchor_still_match"] = False
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["v2_observer_inbox_fixture"])

    def test_v2_observer_inbox_forged_authority_rejected(self):
        for field in (
            "independent_worker_identity_verified",
            "durable_external_anchor_verified", "source_transaction_attested",
            "rollback_resistance_verified", "agent_dispatch_authorized",
        ):
            with self.subTest(field=field):
                e = complete_evidence()
                e["v2_observer_inbox_fixture"][field] = True
                self.assertFalse(create_receipt(e, timestamp="test")[
                    "gate_outcomes"]["v2_observer_inbox_fixture"])

    def test_v2_observer_inbox_must_persist_unverified_quarantine(self):
        e = complete_evidence()
        e["v2_observer_inbox_fixture"][
            "unverified_publication_quarantine_survived_restart"] = False
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["v2_observer_inbox_fixture"])

    def test_v2_observer_inbox_cross_host_rejected(self):
        e = complete_evidence()
        e["v2_observer_inbox_fixture"]["observed_host"] = "destroyer"
        self.assertFalse(create_receipt(e, timestamp="test")[
            "gate_outcomes"]["v2_observer_inbox_fixture"])

    def test_lease_missing_denied(self):
        e = complete_evidence()
        del e["lease"]
        self.assertFalse(create_receipt(e, timestamp="test")["gate_outcomes"]["lease"])

    def test_lease_forwarding_denied(self):
        e = complete_evidence()
        e["lease"]["provider_requests_forwarded"] = 1
        r = create_receipt(e, timestamp="test")
        self.assertFalse(r["gate_outcomes"]["lease"])
        self.assertFalse(r["agent_dispatch_authorized"])

    def test_lease_cross_host_denied(self):
        e = complete_evidence()
        e["lease"]["observed_host"] = "destroyer"
        self.assertFalse(create_receipt(e, timestamp="test")["gate_outcomes"]["lease"])

    def test_lease_revocation_or_replay_failure_denied(self):
        for key in ("revocation_denied", "replay_denied", "epoch_denied"):
            with self.subTest(key=key):
                e = complete_evidence()
                e["lease"][key] = False
                self.assertFalse(create_receipt(e, timestamp="test")["gate_outcomes"]["lease"])

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
