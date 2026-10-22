#!/usr/bin/env python3
"""Ephemeral observer checkpoint + rollback fixture, no live agent inference.

The observer Ed25519 signing key lives in this test process's RAM and is
NEVER persisted. This demonstrates signature and state-check logic only:
the observer is not physically independent or continually publishing.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from durable_lease_store import DurableSyntheticLeaseGate
from observer_checkpoint import (
    ZERO, checkpoint_sha256, public_fingerprint,
    read_local_state_digest, sign_fixture, verify,
)

WORKTREE = hashlib.sha256(b"observer-only-synthetic-source").hexdigest()


def physical() -> dict:
    result = {
        "schema": 1,
        "observed_host": socket.gethostname(),
        "mode": "ephemeral_ed25519_observer_fixture",
        "signed_latest_state_matches": False,
        "restored_old_db_rejected_with_latest_checkpoint": False,
        "stale_checkpoint_rejected_with_trusted_floor": False,
        "old_checkpoint_with_old_floor_still_matches": False,
        "altered_signature_rejected": False,
        "observer_public_key_pin_enforced": False,
        "observer_private_key_persisted": False,
        "physical_observer_custody_verified": False,
        "continuous_publication_verified": False,
        "rollback_resistance_verified": False,
        "provider_requests_forwarded": 0,
        "agent_dispatch_authorized": False,
        "errors": [],
    }
    try:
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="og-observer-", dir=cache) as path:
            root = Path(path)
            os.chmod(root, 0o700)
            database = root / "fixture.sqlite3"
            key = os.urandom(32)
            worker = DurableSyntheticLeaseGate(
                database, key, now=lambda: 1000, create=True)
            scope = {
                "uid": os.getuid(), "owner": "synthetic-owner",
                "session": "synthetic-session",
                "worktree_sha256": WORKTREE, "model_pin": "kilo/kilo-auto/free",
            }
            match = {
                "peer_uid": os.getuid(), "expected_owner": "synthetic-owner",
                "expected_session": "synthetic-session",
                "expected_worktree_sha256": WORKTREE,
                "expected_model_pin": "kilo/kilo-auto/free",
            }
            token = worker.mint(**scope)
            observer = Ed25519PrivateKey.generate()
            public = observer.public_key()
            pin = public_fingerprint(public)
            base = {
                "observer_public_key": public,
                "trusted_observer_fingerprint": pin,
                "expected_observer_id": "independent-role-fixture",
                "expected_worker_id": "worker-role-fixture",
                "state_path": database,
            }
            cp1 = sign_fixture(
                observer_key=observer, observer_id="independent-role-fixture",
                worker_id="worker-role-fixture",
                state_sha256=read_local_state_digest(database),
                generation=1, previous_checkpoint_sha256=ZERO,
                created_unix=1000)
            prior_image = root / "old-valid-snapshot"
            shutil.copy2(database, prior_image)
            consumed = worker.check({
                "schema": 1, "op": "synthetic_receipt",
                "token": token, "seq": 1,
            }, **match)
            cp2 = sign_fixture(
                observer_key=observer, observer_id="independent-role-fixture",
                worker_id="worker-role-fixture",
                state_sha256=read_local_state_digest(database),
                generation=2,
                previous_checkpoint_sha256=checkpoint_sha256(cp1),
                created_unix=1001)
            latest = verify(
                envelope=cp2, min_generation=2,
                trusted_previous_sha256=checkpoint_sha256(cp1), **base)
            result["signed_latest_state_matches"] = (
                consumed.get("status") == "synthetic_receipt_only"
                and latest["signature_valid"] is True
                and latest["checkpoint_matches_sqlite"] is True)
            shutil.copy2(prior_image, database)
            restored = verify(
                envelope=cp2, min_generation=2,
                trusted_previous_sha256=checkpoint_sha256(cp1), **base)
            stale = verify(envelope=cp1, min_generation=2, **base)
            stale_floor = verify(envelope=cp1, min_generation=1, **base)
            result["restored_old_db_rejected_with_latest_checkpoint"] = (
                restored["signature_valid"] is True
                and restored["checkpoint_matches_sqlite"] is False)
            result["stale_checkpoint_rejected_with_trusted_floor"] = (
                stale["signature_valid"] is True
                and stale["checkpoint_generation_acceptable"] is False)
            result["old_checkpoint_with_old_floor_still_matches"] = (
                stale_floor["signature_valid"] is True
                and stale_floor["checkpoint_matches_sqlite"] is True)
            forged = json.loads(json.dumps(cp2))
            forged["payload"]["state_sha256"] = "f" * 64
            result["altered_signature_rejected"] = (
                verify(envelope=forged, min_generation=2, **base)[
                    "signature_valid"] is False)
            wrong_pin = verify(
                envelope=cp1, min_generation=1,
                **{**base, "trusted_observer_fingerprint": "0" * 64})
            result["observer_public_key_pin_enforced"] = (
                wrong_pin["signature_valid"] is False
                and wrong_pin["independent_trust_root_configured"] is False)
            result["observer_private_key_persisted"] = False
            required = (
                "signed_latest_state_matches",
                "restored_old_db_rejected_with_latest_checkpoint",
                "stale_checkpoint_rejected_with_trusted_floor",
                "old_checkpoint_with_old_floor_still_matches",
                "altered_signature_rejected",
                "observer_public_key_pin_enforced",
            )
            if not all(result[k] is True for k in required):
                result["errors"].append("observer fixture proof incomplete")
    except Exception:
        result["errors"].append("observer fixture failed closed")
    return result


def exit_status(report: dict) -> int:
    proof = (
        "signed_latest_state_matches",
        "restored_old_db_rejected_with_latest_checkpoint",
        "stale_checkpoint_rejected_with_trusted_floor",
        "old_checkpoint_with_old_floor_still_matches",
        "altered_signature_rejected",
        "observer_public_key_pin_enforced",
    )
    return 0 if (
        all(report.get(k) is True for k in proof)
        and report.get("observer_private_key_persisted") is False
        and report.get("physical_observer_custody_verified") is False
        and report.get("continuous_publication_verified") is False
        and report.get("rollback_resistance_verified") is False
        and report.get("agent_dispatch_authorized") is False
        and report.get("provider_requests_forwarded") == 0
        and not report.get("errors")
    ) else 2


if __name__ == "__main__":
    result = physical()
    print(json.dumps(result, sort_keys=True, indent=2))
    raise SystemExit(exit_status(result))
