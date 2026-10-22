#!/usr/bin/env python3
"""Two-role, two-process causal fence proof, synthetic/no task dispatch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import hermes_source_claim_census_process_probe as prior
import verify_hermes_causal_claim_fence as causal
import verify_hermes_fenced_quiescence as fence
import verify_hermes_source_owner_census as owner

CHILD = '''
import json,sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import verify_hermes_causal_claim_fence as verifier
args=json.load(sys.stdin)
source=args["source_args"]
source["trusted_source_owner_key"]=Ed25519PublicKey.from_public_bytes(
    bytes.fromhex(source.pop("owner_public_key")))
rev=source["fence_args"]
rev["trusted_authority_key"]=Ed25519PublicKey.from_public_bytes(
    bytes.fromhex(rev.pop("revoker_public_key")))
report=verifier.check(**args)
print(json.dumps({"accepted":report["synthetic_evidence_contract_pass"],
                  "decision":report["decision"],
                  "production_rollback_authorized":report["production_rollback_authorized"]},
                 sort_keys=True))
'''


def pub(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()


def sign_revoker(key: Ed25519PrivateKey, claims: list[str], worker: str,
                 *, issued: int) -> dict:
    record=prior.prior.sign(signer=key,claims=claims,worker=worker,
                            seq=1,predecessor="0"*64)
    record["payload"]["issued_at"]=issued
    record["payload"]["expires_at"]=issued+70
    record["signature"]=key.sign(
        fence.DOMAIN+fence.canonical(record["payload"])).hex()
    return record


def sign_binding(key: Ed25519PrivateKey, census: dict, rev: dict,
                 worker: str) -> dict:
    body={
        "schema":1,"kind":causal.KIND,
        "scope":"hermes-synthetic-owner-revocation",
        "revocation_authority_pin":fence.fingerprint(key.public_key()),
        "worker_pin":worker,
        "source_census_sha256":owner.digest(census),
        "source_census_sequence":census["payload"]["sequence"],
        "revocation_sha256":fence.state_digest(rev),
        "revocation_sequence":rev["payload"]["sequence"],
        "sequence":1,"previous_binding_sha256":"0"*64,
        "issued_at":130,"expires_at":165,
        "resource_effects_fenced":False,
        "independent_custody_verified":False,
        "production_dispatch_authorized":False,"decision":"HOLD",
    }
    return {"algorithm":"Ed25519","payload":body,
            "signature":key.sign(causal.DOMAIN+causal.canonical(body)).hex()}


def invoke(owner_key,revoker_key,census,rev,binding,worker):
    data={
        "source_args":{
            "census":census,
            "owner_public_key":pub(owner_key),
            "expected_owner_pin":owner.fingerprint(owner_key.public_key()),
            "expected_census_digest":owner.digest(census),
            "expected_census_sequence":1,
            "retained_census_minimum":0,
            "expected_census_previous":"0"*64,
            "trusted_now":140,
            "fence_args":{
                "signed_state":rev,
                "revoker_public_key":pub(revoker_key),
                "expected_authority_pin":fence.fingerprint(revoker_key.public_key()),
                "expected_state_digest":fence.state_digest(rev),
                "expected_sequence":1,
                "trusted_minimum_sequence":0,
                "expected_previous_digest":"0"*64,
                "expected_worker_pin":worker,
                "trusted_now":140,
                "quiescence":prior.prior.quiescence(),
                "compose_render":prior.prior.compose(),
                "expected_disposable_image":prior.prior.IMAGE,
            },
        },
        "signed_binding":binding,
        "expected_binding_digest":causal.digest(binding),
        "expected_binding_sequence":1,
        "trusted_binding_minimum":0,
        "expected_previous_binding_digest":"0"*64,
        "trusted_now":140,
    }
    result=subprocess.run(
        [sys.executable,"-B","-c",CHILD],
        input=json.dumps(data),text=True,capture_output=True,
        timeout=15,check=False,
        env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),"HOME":"/tmp",
             "PYTHONDONTWRITEBYTECODE":"1","PYTHONPATH":os.path.dirname(__file__)},
    )
    if result.returncode or len(result.stdout)>2000:
        raise RuntimeError("isolated_causal_verifier_failed_closed")
    parsed=json.loads(result.stdout)
    if (not isinstance(parsed,dict)
            or set(parsed)!={"accepted","decision","production_rollback_authorized"}
            or parsed["decision"]!="HOLD" or parsed["production_rollback_authorized"]):
        raise RuntimeError("unexpected_production_authority")
    return parsed["accepted"] is True


def physical() -> dict:
    owner_key=Ed25519PrivateKey.generate()
    revoker_key=Ed25519PrivateKey.generate()
    if pub(owner_key)==pub(revoker_key):
        raise RuntimeError("signers_not_distinct")
    worker=hashlib.sha256(b"synthetic-worker-causal-fence").hexdigest()
    claims=sorted(hashlib.sha256(k).hexdigest()
                  for k in (b"synthetic-claim-x",b"synthetic-claim-y"))
    census=prior.signed_census(owner_key,claims,worker,1,"0"*64)
    correct=sign_revoker(revoker_key,claims,worker,issued=120)
    earlier=sign_revoker(revoker_key,claims,worker,issued=90)
    positive=sign_binding(revoker_key,census,correct,worker)
    old_binding=sign_binding(revoker_key,census,earlier,worker)
    accepted=invoke(owner_key,revoker_key,census,correct,positive,worker)
    out_of_order=invoke(owner_key,revoker_key,census,earlier,old_binding,worker)
    changed_after_binding=invoke(owner_key,revoker_key,census,earlier,positive,worker)
    if not (accepted and not out_of_order and not changed_after_binding):
        raise RuntimeError("synthetic_causal_matrix_failed")
    return {
        "schema":1,"mode":"hermes_two_process_causal_revocation",
        "synthetic_owner_before_revocation_before_binding_pass":True,
        "valid_prior_revocation_for_same_claims_rejected":True,
        "revocation_swapped_after_binding_rejected":True,
        "separate_verifier_os_process":True,
        "synthetic_signer_timestamps_independent":False,
        "external_head_independently_custodied":False,
        "real_claims_modified":0,
        "production_rollback_authorized":False,"decision":"HOLD",
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-two-process",action="store_true")
    parser.add_argument("--json",action="store_true")
    args=parser.parse_args()
    if args.synthetic_two_process:
        try:
            report=physical()
        except (OSError,ValueError,TypeError,RuntimeError,
                subprocess.TimeoutExpired,json.JSONDecodeError):
            print(json.dumps({"schema":1,"mode":"hermes_two_process_causal_revocation",
                              "synthetic_probe_failed_closed":True,
                              "decision":"HOLD","production_rollback_authorized":False}))
            raise SystemExit(2)
    else:
        report={"schema":1,"mode":"hermes_two_process_causal_revocation",
                "decision":"HOLD","synthetic_probe_not_requested":True,
                "production_rollback_authorized":False}
    print(json.dumps(report,sort_keys=True,indent=2 if args.json else None))


if __name__=="__main__":
    main()
