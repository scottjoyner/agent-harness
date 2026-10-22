#!/usr/bin/env python3
"""Two-signer/two-process synthetic claim census binding; always HOLD."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import verify_hermes_source_owner_census as owner
import verify_hermes_fenced_quiescence as fence
import hermes_fenced_quiescence_process_probe as prior

CHILD = '''
import json,sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import verify_hermes_source_owner_census as module
a=json.load(sys.stdin)
a["trusted_source_owner_key"]=Ed25519PublicKey.from_public_bytes(
    bytes.fromhex(a.pop("owner_public_key")))
f=a["fence_args"]
f["trusted_authority_key"]=Ed25519PublicKey.from_public_bytes(
    bytes.fromhex(f.pop("authority_public_key")))
r=module.check_source_owned_quiescence(**a)
print(json.dumps({"pass":r["synthetic_evidence_contract_pass"],
                  "decision":r["decision"],
                  "production_rollback_authorized":r["production_rollback_authorized"],
                  "owner_census_verified":r["synthetic_owner_census_verified"]},sort_keys=True))
'''


def public_hex(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def signed_census(key, claims, worker, seq, predecessor):
    body = {
        "schema": 1, "kind": "synthetic_source_owner_claim_census",
        "scope": "hermes-synthetic-owner-claims",
        "source_owner_pin": owner.fingerprint(key.public_key()),
        "worker_pin": worker, "sequence": seq,
        "previous_sha256": predecessor,
        "issued_at": 100, "expires_at": 160,
        "active_claim_sha256": claims, "claim_set_sha256": owner.claim_digest(claims),
        "resource_side_effects_attested": False,
        "independent_custody_verified": False,
        "source_owner_admission_authorized": False, "decision": "HOLD",
    }
    return {"algorithm":"Ed25519","payload":body,
            "signature":key.sign(owner.DOMAIN+owner.canonical(body)).hex()}


def run_child(census, revocation, *, owner_key, revoker_key,
              seq, source_floor, source_prev, fence_floor, fence_prev,
              source_hash, fence_hash, worker):
    args = {
        "census":census, "owner_public_key":public_hex(owner_key),
        "expected_owner_pin":owner.fingerprint(owner_key.public_key()),
        "expected_census_digest":source_hash,
        "expected_census_sequence":seq,
        "retained_census_minimum":source_floor,
        "expected_census_previous":source_prev,
        "trusted_now":110,
        "fence_args":{
            "signed_state":revocation,
            "authority_public_key":public_hex(revoker_key),
            "expected_authority_pin":fence.fingerprint(revoker_key.public_key()),
            "expected_state_digest":fence_hash,
            "expected_sequence":seq,
            "trusted_minimum_sequence":fence_floor,
            "expected_previous_digest":fence_prev,
            "expected_worker_pin":worker,
            "trusted_now":110,
            "quiescence":prior.quiescence(),
            "compose_render":prior.compose(),
            "expected_disposable_image":prior.IMAGE,
        },
    }
    proc=subprocess.run(
        [sys.executable,"-B","-c",CHILD],
        input=json.dumps(args),text=True,capture_output=True,timeout=15,
        env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),
             "HOME":"/tmp","PYTHONDONTWRITEBYTECODE":"1",
             "PYTHONPATH":os.path.dirname(__file__)},
    )
    if proc.returncode or len(proc.stdout)>2000:
        raise RuntimeError("isolated_verifier_failed_closed")
    out=json.loads(proc.stdout)
    if not isinstance(out,dict) or set(out)!={
            "pass","decision","production_rollback_authorized","owner_census_verified"
    } or out["decision"]!="HOLD" or out["production_rollback_authorized"]:
        raise RuntimeError("invalid_or_escalated_source_census_result")
    return out


def physical() -> dict:
    source_key=Ed25519PrivateKey.generate()
    revoker_key=Ed25519PrivateKey.generate()
    if public_hex(source_key)==public_hex(revoker_key):
        raise RuntimeError("roles_not_distinct")
    worker=hashlib.sha256(b"synthetic-census-bound-worker").hexdigest()
    claim_a=hashlib.sha256(b"owner-assigned-1").hexdigest()
    claim_b=hashlib.sha256(b"owner-assigned-2").hexdigest()
    old_claims=[claim_a]
    current_claims=sorted([claim_a,claim_b])
    census_old=signed_census(source_key,old_claims,worker,1,"0"*64)
    rev_old=prior.sign(signer=revoker_key,claims=old_claims,
                       worker=worker,seq=1,predecessor="0"*64)
    old_source=owner.digest(census_old)
    old_rev=fence.state_digest(rev_old)
    census_new=signed_census(source_key,current_claims,worker,2,old_source)
    rev_new=prior.sign(signer=revoker_key,claims=current_claims,
                       worker=worker,seq=2,predecessor=old_rev)
    source_head=owner.digest(census_new)
    rev_head=fence.state_digest(rev_new)
    common=dict(owner_key=source_key,revoker_key=revoker_key,worker=worker)
    fresh=run_child(census_new,rev_new,seq=2,source_floor=1,
                    source_prev=old_source,fence_floor=1,fence_prev=old_rev,
                    source_hash=source_head,fence_hash=rev_head,**common)
    absent=prior.sign(signer=revoker_key,claims=old_claims,
                      worker=worker,seq=2,predecessor=old_rev)
    absent_result=run_child(census_new,absent,seq=2,source_floor=1,
                            source_prev=old_source,fence_floor=1,fence_prev=old_rev,
                            source_hash=source_head,
                            fence_hash=fence.state_digest(absent),**common)
    stale=run_child(census_old,rev_old,seq=2,source_floor=1,
                    source_prev=old_source,fence_floor=1,fence_prev=old_rev,
                    source_hash=source_head,fence_hash=rev_head,**common)
    # Prove owner+revocation state and BOTH trusted heads/floors can be
    # rewound together when their custodians are not independent.
    rewind=run_child(census_old,rev_old,seq=1,source_floor=0,
                     source_prev="0"*64,fence_floor=0,fence_prev="0"*64,
                     source_hash=old_source,fence_hash=old_rev,**common)
    if not (fresh["pass"] and fresh["owner_census_verified"]
            and not absent_result["pass"] and not stale["pass"]
            and rewind["pass"]):
        raise RuntimeError("signed_two_role_claim_census_matrix_failed")
    return {
        "schema":1,"mode":"hermes_two_process_two_role_claim_census",
        "fresh_two_role_signed_claim_set_pass":True,
        "omitted_revoked_claim_denied":True,
        "stale_owner_census_denied_with_new_head":True,
        "both_heads_rollback_counterexample_reproduced":True,
        "separate_verifier_os_process":True,
        "source_owner_independently_administered":False,
        "revocation_authority_independently_administered":False,
        "real_claims_queried_or_modified":0,
        "production_rollback_authorized":False,
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
            print(json.dumps({"mode":"hermes_two_process_two_role_claim_census",
                              "decision":"HOLD","synthetic_probe_failed_closed":True,
                              "production_rollback_authorized":False}))
            raise SystemExit(2)
    else:
        result={"schema":1,"mode":"hermes_two_process_two_role_claim_census",
                "decision":"HOLD","synthetic_probe_not_requested":True,
                "production_rollback_authorized":False}
    print(json.dumps(result,sort_keys=True,indent=2 if args.json else None))


if __name__=="__main__":
    main()
