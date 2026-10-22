import hashlib
import unittest
from copy import deepcopy

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from observer_checkpoint import public_fingerprint
from worker_acceptance_receipt import sign_fixture, verify

def sha(data):
    return hashlib.sha256(data).hexdigest()

BASE = {
    "worker_id": "lenovo-ack-worker",
    "observer_id": "x1-ack-observer",
    "operation_id": "a" * 32,
    "request_sha256": sha(b"request"),
    "prepare_sha256": sha(b"prepare"),
    "before_state_sha256": sha(b"before"),
    "after_state_sha256": sha(b"after"),
    "generation": 2,
    "sequence": 1,
    "receipt_number": 1,
    "agent_dispatch_authorized": False,
    "provider_requests_forwarded": 0,
}


class SignedResourceReceiptTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.pin = public_fingerprint(self.key.public_key())
        self.other = Ed25519PrivateKey.generate()
        self.commit = sign_fixture(
            signer=self.key, kind="synthetic_worker_commit", fields=BASE)

    def check(self, record=None, **overrides):
        return verify(
            envelope=self.commit if record is None else record,
            public_key=overrides.pop("public_key", self.key.public_key()),
            trusted_fingerprint=overrides.pop("trusted_fingerprint", self.pin),
            expected_kind=overrides.pop("expected_kind", "synthetic_worker_commit"),
            expected=overrides.pop("expected", BASE),
            observer_fingerprint=overrides.pop("observer_fingerprint", public_fingerprint(self.other.public_key())),
            **overrides)

    def test_commit_success_does_not_authorize_agent(self):
        self.assertTrue(self.check())
        self.assertFalse(self.commit["payload"]["agent_dispatch_authorized"])
        self.assertEqual(self.commit["payload"]["provider_requests_forwarded"], 0)

    def test_other_public_key_denied(self):
        self.assertFalse(self.check(public_key=self.other.public_key()))

    def test_other_fingerprint_denied(self):
        self.assertFalse(self.check(trusted_fingerprint=public_fingerprint(self.other.public_key())))

    def test_worker_key_same_as_observer_denied(self):
        self.assertFalse(self.check(observer_fingerprint=self.pin))

    def test_exact_scope_mismatch_denied(self):
        for field, value in {
            "operation_id": "b" * 32, "request_sha256": "0" * 64,
            "prepare_sha256": "0" * 64,
            "before_state_sha256": "0" * 64,
            "after_state_sha256": "0" * 64,
            "generation": 3, "sequence": 2, "receipt_number": 2,
            "worker_id": "destroyer", "observer_id": "other",
        }.items():
            with self.subTest(field=field):
                self.assertFalse(self.check(expected={**BASE, field: value}))

    def test_tampered_signature_denied(self):
        altered = deepcopy(self.commit)
        altered["payload"]["sequence"] = 2
        self.assertFalse(self.check(record=altered))

    def test_duplicate_keys_or_extra_claims_denied(self):
        altered = deepcopy(self.commit)
        altered["payload"]["debug"] = "hello"
        self.assertFalse(self.check(record=altered))

    def test_missing_expectation_denied(self):
        expected = dict(BASE)
        del expected["before_state_sha256"]
        self.assertFalse(self.check(expected=expected))

    def test_boolean_sequence_not_integer(self):
        with self.assertRaises(ValueError):
            sign_fixture(signer=self.key, kind="synthetic_worker_commit",
                         fields={**BASE, "sequence": True})

    def test_dispatch_claim_denied(self):
        with self.assertRaises(ValueError):
            sign_fixture(signer=self.key, kind="synthetic_worker_commit",
                         fields={**BASE, "agent_dispatch_authorized": True})

    def test_nonzero_provider_forwarding_denied(self):
        with self.assertRaises(ValueError):
            sign_fixture(signer=self.key, kind="synthetic_worker_commit",
                         fields={**BASE, "provider_requests_forwarded": 1})

    def test_signing_extra_fields_denied(self):
        with self.assertRaises(ValueError):
            sign_fixture(signer=self.key, kind="synthetic_worker_commit",
                         fields={**BASE, "extra": "unexpected"})

    def test_after_state_must_differ(self):
        with self.assertRaises(ValueError):
            sign_fixture(signer=self.key, kind="synthetic_worker_commit",
                         fields={**BASE, "after_state_sha256": BASE["before_state_sha256"]})

    def test_delivery_receipt_checks_exact_ack_and_checkpoint(self):
        d = {
            **BASE,
            "observer_ack_sha256": sha(b"ACK"),
            "observer_checkpoint_sha256": sha(b"checkpoint"),
        }
        signed = sign_fixture(signer=self.key,
                              kind="synthetic_worker_delivery", fields=d)
        self.assertTrue(self.check(record=signed,
                                   expected_kind="synthetic_worker_delivery",
                                   expected=d))
        self.assertFalse(self.check(record=signed,
                                    expected_kind="synthetic_worker_delivery",
                                    expected={**d, "observer_ack_sha256": "f" * 64}))
        self.assertFalse(self.check(record=signed,
                                    expected_kind="synthetic_worker_commit",
                                    expected=BASE))

    def test_commit_cannot_be_substituted_for_delivery(self):
        delivery = {
            **BASE,
            "observer_ack_sha256": sha(b"ACK"),
            "observer_checkpoint_sha256": sha(b"checkpoint"),
        }
        self.assertFalse(self.check(
            record=self.commit,
            expected_kind="synthetic_worker_delivery",
            expected=delivery))

    def test_unrecognized_receipt_kind_rejected(self):
        with self.assertRaises(ValueError):
            sign_fixture(signer=self.key, kind="agent_dispatch",
                         fields=BASE)
        self.assertFalse(self.check(expected_kind="agent_dispatch"))

    def test_noncanonical_signature_encoding_rejected(self):
        original = self.commit["signature"]
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        pos = alphabet.index(original[-1])
        changed = alphabet[(pos & ~3) | ((pos + 1) & 3)]
        altered = deepcopy(self.commit)
        altered["signature"] = original[:-1] + changed
        self.assertFalse(self.check(record=altered))

    def test_verified_signature_not_replayable_for_new_sequence(self):
        self.assertFalse(self.check(expected={**BASE, "sequence": 2}))

    def test_wrong_generation_or_role_cannot_be_signed(self):
        for changes in (
            {"generation": True},
            {"generation": 1},
            {"worker_id": BASE["observer_id"]},
            {"worker_id": "worker bad"},
            {"operation_id": "invalid"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    sign_fixture(signer=self.key,
                        kind="synthetic_worker_commit",
                        fields={**BASE, **changes})

    def test_invalid_or_missing_signature_rejected(self):
        self.assertFalse(self.check(record={}))
        self.assertFalse(self.check(record=None) is False)
        altered = deepcopy(self.commit)
        altered["signature"] = "invalid"
        self.assertFalse(self.check(record=altered))


if __name__ == "__main__":
    unittest.main()
