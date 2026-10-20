"""Disposable-only fourth signing role; never independent trust custody."""
from __future__ import annotations

import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from observer_checkpoint import public_fingerprint
from v2_custody_checkpoint_contract import (
    checkpoint_digest, verify_research_checkpoint,
)
from v2_custody_signer_fixture import (
    UnsafeResearchCustodian, _execute, _load_key, _root_ok,
    _no_duplicate_fields,
)
from test_v2_custody_checkpoint_contract import CustodyCheckpointTests


def raw64(key):
    import base64
    return base64.b64encode(key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw)).decode("ascii")


class IsolatedCustodySignerTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="og-v2-custody-test-")
        self.addCleanup(t.cleanup)
        self.home = Path(t.name)
        self.cache = self.home / ".cache"
        self.cache.mkdir(mode=0o700)
        self.root = self.cache / "og-v2-custody-Ab19zQ"
        self.root.mkdir(mode=0o700)
        mockhome = patch.object(Path, "home", return_value=self.home)
        mockhome.start()
        self.addCleanup(mockhome.stop)

    def test_bootstrap_is_private_and_non_authorizing(self):
        boot = _execute(self.root, command="bootstrap", request={})
        self.assertEqual(boot["decision"], "HOLD")
        self.assertFalse(boot["agent_dispatch_authorized"])
        self.assertEqual(
            (self.root / "custodian-ephemeral.key").stat().st_mode & 0o777,
            0o600)
        self.assertEqual(
            public_fingerprint(_load_key(self.root, create=False).public_key()),
            boot["custodian_fingerprint"])

    def test_restart_key_is_stable_and_duplicate_bootstrap_refused(self):
        before = _execute(self.root, command="bootstrap", request={})
        after = _load_key(self.root, create=False)
        self.assertEqual(
            public_fingerprint(after.public_key()),
            before["custodian_fingerprint"])
        with self.assertRaises(FileExistsError):
            _execute(self.root, command="bootstrap", request={})

    def test_role_signer_accepts_only_real_signed_synthetic_witness(self):
        # Reuse exactly the separately tested V2 witness fixture's
        # signed initial state, never claim live source authority.
        env = CustodyCheckpointTests(methodName="test_exact_detached_signature_requires_four_role_pins")
        env.setUp()
        self.addCleanup(env.doCleanups)
        _execute(self.root, command="bootstrap", request={})
        request = {
            "witness_receipt": env.witness_receipt,
            "witness_public_base64": raw64(env.witness.public_key()),
            "witness_pin": env.witness_pin,
            "observer_public_base64": raw64(env.observer.public_key()),
            "worker_fingerprint": env.worker_pin,
            "observer_id": "observer-test", "worker_id": "worker-test",
            "witness_head": list(env.witness_head),
            "observer_head": list(env.obs_head),
            "scope": "herdr-omnigent.synthetic.v2",
            "generation": 1,
            "previous_checkpoint_sha256": "0"*64,
            "issued_at": 1700000000, "expires_at": 1700000600,
        }
        signed = _execute(self.root, command="sign", request=request)
        checkpoint = signed["checkpoint"]
        self.assertTrue(verify_research_checkpoint(
            checkpoint,
            trusted_custodian_key=_load_key(
                self.root, create=False).public_key(),
            expected_custodian_pin=public_fingerprint(
                _load_key(self.root, create=False).public_key()),
            witness_receipt=env.witness_receipt,
            trusted_witness_key=env.witness.public_key(),
            expected_witness_pin=env.witness_pin,
            observer_public_key=env.observer.public_key(),
            worker_fingerprint=env.worker_pin,
            observer_id="observer-test", worker_id="worker-test",
            expected_witness_head=env.witness_head,
            expected_observer_head=env.obs_head,
            expected_scope="herdr-omnigent.synthetic.v2",
            expected_generation=1,
            expected_previous_checkpoint_sha256="0"*64,
            expected_checkpoint_sha256=checkpoint_digest(checkpoint),
            trusted_now=1700000020,
            minimum_witness_count=0))
        again = _execute(self.root, command="sign", request=request)
        self.assertEqual(signed, again)
        changed = deepcopy(request)
        changed["witness_receipt"]["signature"] = "forged"
        from v2_custody_checkpoint_contract import UnsafeCustodyCheckpoint
        with self.assertRaises(UnsafeCustodyCheckpoint):
            _execute(self.root, command="sign", request=changed)

    def test_wrong_root_or_symlink_denied(self):
        for pathname in (
            self.home / "production",
            self.cache / "og-v2-custody-ABCD",
            Path("/tmp/og-v2-custody-Ab19zQ"),
        ):
            with self.subTest(path=pathname):
                with self.assertRaises(UnsafeResearchCustodian):
                    _root_ok(pathname)
        alias = self.cache / "og-v2-custody-QQQQQQ"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(UnsafeResearchCustodian):
            _root_ok(alias)

    def test_other_user_readable_root_denied(self):
        self.root.chmod(0o755)
        with self.assertRaises(UnsafeResearchCustodian):
            _execute(self.root, command="bootstrap", request={})

    def test_private_key_hardlink_and_permissions_denied(self):
        _execute(self.root, command="bootstrap", request={})
        key = self.root / "custodian-ephemeral.key"
        os.link(key, self.root / "keylink")
        with self.assertRaises(UnsafeResearchCustodian):
            _load_key(self.root, create=False)
        (self.root / "keylink").unlink()
        key.chmod(0o644)
        with self.assertRaises(UnsafeResearchCustodian):
            _load_key(self.root, create=False)

    def test_missing_or_unknown_claim_denied(self):
        _execute(self.root, command="bootstrap", request={})
        for malformed in ({}, {"provider": "auto-router"}, [], None):
            with self.subTest(case=malformed):
                with self.assertRaises(UnsafeResearchCustodian):
                    _execute(self.root, command="sign", request=malformed)

    def test_non_data_bootstrap_rejected(self):
        with self.assertRaises(UnsafeResearchCustodian):
            _execute(self.root, command="bootstrap", request={"model": "load"})

    def test_non_canonic_duplicate_json_fields_denied(self):
        with self.assertRaises(UnsafeResearchCustodian):
            _no_duplicate_fields([("schema", 1), ("schema", 2)])
        self.assertEqual(_no_duplicate_fields([("schema", 1)]), {"schema": 1})

    def test_no_private_key_in_bootstrap_output(self):
        reply = _execute(self.root, command="bootstrap", request={})
        self.assertNotIn("private", str(reply).lower())
        self.assertNotIn("secret", str(reply).lower())


if __name__ == "__main__":
    unittest.main()
