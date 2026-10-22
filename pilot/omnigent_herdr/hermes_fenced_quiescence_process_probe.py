#!/usr/bin/env python3
"""Disposable two-process synthetic Hermes revoked-claim quiescence proof.

The private Ed25519 signer exists only in the parent process memory.
The verifying child receives only public key and signed synthetic state,
with separately supplied checkpoint pins. No Docker, network, provider,
task API, real claims, credentials or production data are touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import verify_hermes_fenced_quiescence as gate

IMAGE = "og-hermes-quiescent-base:synthetictest1234"
CHILD = '''
import json,sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import verify_hermes_fenced_quiescence as gate
p=json.load(sys.stdin)
p["trusted_authority_key"]=Ed25519PublicKey.from_public_bytes(
    bytes.fromhex(p.pop("authority_public_key")))
result=gate.evaluate(**p)
print(json.dumps({"pass":result["synthetic_evidence_contract_pass"],
                  "violations":result["violations"],
                  "production_rollback_authorized":result["production_rollback_authorized"],
                  "decision":result["decision"]},sort_keys=True))
'''


def compose():
    return {"name": "temporary-quiescent-research",
            "services": {"hermes-quiescent-synthetic": {
                "profiles": ["quiescent-canary"], "image": IMAGE,
                "pull_policy": "never", "entrypoint": ["/bin/sleep"],
                "command": ["75"], "restart": "no", "user": "1000:1000",
                "read_only": True, "network_mode": "none", "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "pids_limit": 24, "mem_limit": "134217728", "cpus": 0.5,
            }}}


def quiescence():
    return {"schema":1,"candidate_absent":True,"restart_disabled":True,
            "claims_zero":True,"inflight_effects_zero":True,
            "provider_requests_zero":True,
            "legacy_restore_forbidden":True,"scratch_intact":True}


def sign(*, signer, claims, worker, seq, predecessor):
    body = {
        "schema":1, "kind":gate.KIND,"scope":"hermes-synthetic-no-dispatch",
        "authority_pin":gate.fingerprint(signer.public_key()),
        "worker_pin":worker,"sequence":seq,"previous_state_sha256":predecessor,
        "issued_at":100,"expires_at":160,
        "revoked_claim_sha256":claims,
        "claim_set_sha256":gate.claimset_digest(claims),
        "pending_claims":0,"inflight_effects":0,"provider_requests":0,
        "side_effects_attested":False,
        "independent_custody_verified":False,
        "production_dispatch_allowed":False,"decision":"HOLD"}
    return {"algorithm":"Ed25519","payload":body,
            "signature":signer.sign(gate.DOMAIN+gate.canonical(body)).hex()}


def invoke(state, signer, *, claims, sequence, floor, previous, pinned_digest):
    args = {
        "signed_state":state,
        "authority_public_key":signer.public_key().public_bytes(
            Encoding.Raw,PublicFormat.Raw).hex(),
        "expected_authority_pin":gate.fingerprint(signer.public_key()),
        "expected_state_digest":pinned_digest,
        "expected_sequence":sequence,
        "trusted_minimum_sequence":floor,
        "expected_previous_digest":previous,
        "expected_worker_pin":state["payload"]["worker_pin"],
        "expected_revoked_claim_sha256":claims,
        "trusted_now":110,
        "quiescence":quiescence(),
        "compose_render":compose(),
        "expected_disposable_image":IMAGE,
    }
    proc = subprocess.run([sys.executable,"-B","-c",CHILD],
                          input=json.dumps(args),capture_output=True,
                          text=True,timeout=12,check=False,
                          env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),
                               "PYTHONPATH":os.path.dirname(__file__),
                               "PYTHONDONTWRITEBYTECODE":"1",
                               "HOME":"/tmp"})
    if proc.returncode != 0 or len(proc.stdout)>2000:
        raise RuntimeError("synthetic_child_failed_closed")
    result=json.loads(proc.stdout)
    if not isinstance(result,dict) or set(result)!={
            "pass","violations","production_rollback_authorized","decision"}:
        raise RuntimeError("unexpected_child_result")
    if result["decision"]!="HOLD" or result["production_rollback_authorized"]:
        raise RuntimeError("unexpected_release_authority")
    return result


def physical() -> dict:
    signer=Ed25519PrivateKey.generate()
    worker=hashlib.sha256(b"synthetic-bound-worker").hexdigest()
    a=hashlib.sha256(b"synthetic-claim-a").hexdigest()
    b=hashlib.sha256(b"synthetic-claim-b").hexdigest()
    one=[a]
    two=sorted([a,b])
    old=sign(signer=signer,claims=one,worker=worker,seq=1,predecessor="0"*64)
    old_head=gate.state_digest(old)
    new=sign(signer=signer,claims=two,worker=worker,seq=2,predecessor=old_head)
    new_head=gate.state_digest(new)
    ok=invoke(new,signer,claims=two,sequence=2,floor=1,
              previous=old_head,pinned_digest=new_head)
    stale=invoke(old,signer,claims=two,sequence=2,floor=1,
                 previous=old_head,pinned_digest=new_head)
    omitted=invoke(new,signer,claims=one,sequence=2,floor=1,
                   previous=old_head,pinned_digest=new_head)
    # CRITICAL counterexample: an attacker rewinds BOTH the valid
    # signed state and the expected trusted head/floor.
    rolled=invoke(old,signer,claims=one,sequence=1,floor=0,
                  previous="0"*64,pinned_digest=old_head)
    if not (ok["pass"] and not stale["pass"] and not omitted["pass"]
            and rolled["pass"]):
        raise RuntimeError("synthetic_fencing_matrix_rejected")
    return {
        "schema":1,"mode":"hermes_two_process_synthetic_fence",
        "fresh_pinned_revocation_pass":True,
        "stale_signed_replay_rejected_against_new_head":True,
        "missing_revoked_claim_rejected":True,
        "rollback_of_head_and_floor_counterexample_reproduced":True,
        "distinct_verifier_process":True,
        "independent_authority_custody":False,
        "production_rollback_authorized":False,
        "real_task_claims_processed":0,"provider_requests_forwarded":0,
        "decision":"HOLD",
    }


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--synthetic-two-process",action="store_true")
    ap.add_argument("--json",action="store_true")
    args=ap.parse_args()
    if args.synthetic_two_process:
        try:
            result=physical()
        except (OSError,ValueError,TypeError,RuntimeError,
                subprocess.TimeoutExpired,json.JSONDecodeError):
            result={"mode":"hermes_two_process_synthetic_fence",
                    "schema":1,"decision":"HOLD",
                    "synthetic_process_probe_failed_closed":True,
                    "production_rollback_authorized":False}
            print(json.dumps(result,sort_keys=True))
            raise SystemExit(2)
    else:
        result={"schema":1,"mode":"hermes_two_process_synthetic_fence",
                "decision":"HOLD","synthetic_probe_not_requested":True,
                "production_rollback_authorized":False}
    print(json.dumps(result,sort_keys=True,indent=2 if args.json else None))


if __name__=="__main__":
    main()
