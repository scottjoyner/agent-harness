#!/usr/bin/env python3
"""Offline capability evidence -> deny-only fleet recommendation projection.

Never imports provider SDKs, sends inference, edits routes, acquires quota, or
creates autonomous tasks. Only reads existing synthetic campaign receipts.
"""
from __future__ import annotations

import argparse
import ast
import collections
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parent
SCHEMA = "free-model-capability-shadow/v1"
SOURCES = (
    ("smoke", "live-research/*/receipt.json"),
    ("openrouter_sweep", "sweeps/*/results/*.json"),
    ("kilo_sweep", "kilo-sweeps/*/*.json"),
    ("role", "agent-roles/*/*.json"),
    ("recovery", "role-recovery/*/*.json"),
)
ROOT_GROUPS = {"OpenRouter": "openrouter-upstream-UNKNOWN",
               "Cohere DIRECT": "cohere-direct-UNKNOWN",
               "Kilo anonymous": "kilo-physical-upstream-UNKNOWN"}

def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

def stable(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()

def canonical_model(source: str, obj: dict) -> tuple[str, str]:
    if source in ("smoke", "openrouter_sweep"):
        provider = obj.get("provider")
        product = "OpenRouter" if provider == "openrouter" else (
            "Cohere DIRECT" if provider == "cohere" else None)
    elif source == "kilo_sweep":
        product = "Kilo anonymous"
    else:
        provider = obj.get("provider")
        product = "Kilo anonymous" if provider == "kilo" else (
            "Cohere DIRECT" if provider == "cohere" else None)
    model = obj.get("exact_model_id") or obj.get("model")
    if product is None or not isinstance(model, str) or not model or "/" in model and model.startswith("/"):
        raise ValueError("unknown or malformed model product/identity")
    return product, model

def score_json(s: str) -> bool:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^\s*```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    try: v = json.loads(s)
    except (TypeError, ValueError): return False
    return isinstance(v, dict) and v.get("values") == [1, 3, 5] and v.get("sum") == 9

def scored_role(role: str, text: str) -> bool:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^\s*```(?:python|json|diff)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    if role == "patch-reviewer":
        return "-    for i in range(len(xs)-1):" in text and "+    for i in range(len(xs)):" in text
    if role == "security-auditor":
        try:
            obj = json.loads(text)
            return (obj.get("approve") is False and
                    "disable" in obj.get("safe_fix", "").lower())
        except (TypeError, ValueError, AttributeError): return False
    if role == "quota-auditor":
        try:
            obj = json.loads(text)
            return obj.get("admitted") is False and obj.get("maximum_shared_requests") == 50
        except (TypeError, ValueError, AttributeError): return False
    if role in ("coder", "coder-recovery", "coder-cohere", "coder-cohere-v2"):
        try:
            tree = ast.parse(text)
            return any(isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and x.name == "merge_intervals" for x in tree.body)
        except SyntaxError: return False
    if role in ("test-designer", "test-designer-recovery"):
        try:
            obj=json.loads(text)
            cases=obj.get("cases", [])
            if not isinstance(cases, list) or len(cases)<3: return False
            kinds=set()
            for c in cases:
                if not isinstance(c,dict) or not all(k in c for k in ("input","expected")):
                    return False
                if not isinstance(c["input"],list) or not isinstance(c["expected"],list):
                    return False
                if not c["input"]: kinds.add("empty")
                if len(c["input"])>1: kinds.add("multiple")
                if len(c["expected"])<len(c["input"]): kinds.add("merged")
            return {"empty","multiple","merged"}.issubset(kinds)
        except (ValueError,TypeError,AttributeError): return False
    return False

def output_path(src: str, file: Path, obj: dict) -> Path | None:
    if src == "smoke": return file.parent / "synthetic-completion.txt"
    if src == "openrouter_sweep": return file.with_suffix(".synthetic-output.txt")
    if src == "kilo_sweep": return file.with_suffix(".synthetic.txt")
    if src in ("role","recovery"):
        return file.parent / (obj["role"]+".synthetic.txt")
    return None

def qualified_secondary_evidence(role: str, source: Path, root: Path) -> bool:
    """Bind an external offline validator to the exact saved generated output."""
    def valid_file(path: Path, expected_code: bytes, expected_suite: bytes, required_cases: int) -> bool:
        if not path.is_file(): return False
        result=json.loads(path.read_text())
        return (result.get('sandbox_ok') is True and result.get('all_pass') is True and
                result.get('cases')==required_cases and
                result.get('correct')==required_cases and
                result.get('nonmutating')==required_cases and
                result.get('canonical_output')==required_cases and
                result.get('code_sha256')==sha(expected_code) and
                result.get('test_suite_sha256')==sha(expected_suite) and
                result.get('network')=='none' and result.get('mounts')=='none' and
                result.get('rootfs')=='read-only' and result.get('uid')=='65534:65534')
    if role=='coder-cohere-v2':
        code=source.parent/'coder-cohere-v2.synthetic.txt'
        suite=root/'role-recovery/20261022T172056Z/test-designer-recovery.synthetic.txt'
        heldout=root/'heldout_merge_intervals_cases.json'
        if not all(p.is_file() for p in (code,suite,heldout)):return False
        return (valid_file(source.parent/'coder-cohere-v2-sandbox-check.json',
                           code.read_bytes(),suite.read_bytes(),12) and
                valid_file(source.parent/'coder-cohere-heldout-v2.json',
                           code.read_bytes(),heldout.read_bytes(),20))
    if role=='test-designer-recovery':
        produced=source.parent/'test-designer-recovery.synthetic.txt'
        checked=source.parent/'test-designer-recovery-validated.json'
        if not produced.is_file() or not checked.is_file(): return False
        j=json.loads(checked.read_text())
        return (j.get('source_sha256')==sha(produced.read_bytes()) and
                j.get('input_cases')==12 and j.get('correct')==12 and
                j.get('failures')==0)
    return False

def verify_receipt(src: str, path: Path, root: Path) -> dict:
    obj = json.loads(path.read_text())
    product, model = canonical_model(src, obj)
    role = obj.get("role","json-sort")
    h = (obj.get("request_json_sha256") or obj.get("request_sha256"))
    if not re.fullmatch(r"[0-9a-f]{64}", h or ""):
        raise ValueError("missing/invalid request SHA256 receipt: "+str(path))
    timestamp = obj.get("timestamp_utc") or obj.get("date_utc") or obj.get("utc")
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("request timestamp missing: "+str(path))
    status = obj.get("http_status")
    if status is not None and not isinstance(status,int): raise ValueError("malformed HTTP status")
    p = output_path(src,path,obj)
    text = p.read_text() if p is not None and p.exists() else ""
    expected_hash = (obj.get("completion_text_sha256") or obj.get("output_sha256") or
                     obj.get("response_text_sha256") or obj.get("visible_text_sha256"))
    hash_verified = bool(expected_hash and sha(text.encode()) == expected_hash)
    visible_chars = obj.get("returned_chars",obj.get("output_visible_characters",
                   obj.get("visible_chars",obj.get("output_chars",0)))) or 0
    if status == 200 and visible_chars and not hash_verified:
        raise ValueError("nonempty successful output missing or digest mismatch: "+str(path))
    usage = obj.get("usage")
    cost = obj.get("reported_cost", "UNKNOWN")
    reported_zero = cost in (0,0.0,"0","0.0")
    paid_or_unknown = not reported_zero
    if src == "smoke" and obj.get("request_accepted_and_returned_text") is False:
        # Detect reasoning-only completions; not a successful task response.
        pass
    served = obj.get("served_model_id",obj.get("model_returned",obj.get("response_model_id",obj.get("returned_model"))))
    # Some providers (notably Cohere direct) omit the served ID entirely.
    # A correct answer can be observed even when exact served-model attribution
    # remains unproven. Explicitly contradictory served IDs reject the result.
    model_exact = bool(served and served != "UNKNOWN" and served == model)
    model_mismatch = bool(served and served != "UNKNOWN" and served != model)
    correct = bool(status==200 and text and hash_verified and not model_mismatch and
                   (score_json(text) if role=="json-sort" else scored_role(role,text)))
    requires_secondary=(role.startswith('coder') or role.startswith('test-designer'))
    independent_secondary_test_passed=(qualified_secondary_evidence(role,path,root)
                                       if requires_secondary else False)
    if requires_secondary:
        # A function name or plausible test JSON is not evidence of correctness.
        correct = correct and independent_secondary_test_passed
    receipt = {
        "product":product,"exact_model_id":model,"role":role,
        "utc":timestamp,"http_status":status,"request_sha256":h,
        "receipt_sha256":sha(path.read_bytes()),"receipt_path":str(path.relative_to(root)),
        "output_sha256_verified":hash_verified,"visible_chars":len(text),
        "usage_present":isinstance(usage,dict) and bool(usage),
        "reported_zero_cost":reported_zero,"monetary_cost_unverified":paid_or_unknown,
        "served_model_exact":model_exact,"served_model_conflict":model_mismatch,
        "provider_served_model_claim":served or "UNKNOWN", "objective_pass":correct,
        "independent_secondary_test_passed":independent_secondary_test_passed,
        "objective_scoring_kind":("independent_sandbox_or_reference" if requires_secondary
                                  else "synthetic_output_reference_or_pattern"),
        "failure_or_incomplete": not correct,
        "shared_quota_group":ROOT_GROUPS.get(product,"UNKNOWN"),
        "source":src, "execution_is_research_only":True,
    }
    if status is None: receipt["transport_error"]=obj.get("exception_type",obj.get("error_type","UNKNOWN"))
    if status and status != 200: receipt["http_error_class"]=obj.get("error_class","HTTP_"+str(status))
    return receipt

def collect(root: Path) -> list[dict]:
    receipts=[]
    for source,pattern in SOURCES:
        for path in sorted(root.glob(pattern)):
            if path.name=="summary.json": continue
            if source=="recovery" and path.stem not in (
                "coder-recovery", "test-designer-recovery", "coder-cohere", "coder-cohere-v2"):
                # Sandbox receipts and test-result JSON are not API requests.
                continue
            receipts.append(verify_receipt(source,path,root))
    return receipts

def project(receipts: list[dict]) -> dict:
    by=collections.defaultdict(list)
    for r in receipts: by[(r["product"],r["exact_model_id"])].append(r)
    models=[]
    for (product,model), group in sorted(by.items()):
        tasks=collections.defaultdict(lambda:{"attempts":0,"objective_passes":0,"output_digests":[]})
        for r in group:
            q=tasks[r["role"]]
            q["attempts"]+=1
            q["objective_passes"]+=int(r["objective_pass"])
            if r["objective_pass"]:
                q["output_digests"].append(r["receipt_sha256"])
        role_evidence={k:dict(v) for k,v in sorted(tasks.items())}
        failures=[r for r in group if r["http_status"] in (None,401,402,403,404,429)]
        audit={
            "product":product,"model":model,"route_key":product+"|"+model,
            "shared_quota_group":group[0]["shared_quota_group"],
            "attempted":len(group),"http200":sum(r["http_status"]==200 for r in group),
            "objective_passes":sum(r["objective_pass"] for r in group),
            "zero_cost_receipts":sum(r["reported_zero_cost"] for r in group),
            "unknown_cost_receipts":sum(r["monetary_cost_unverified"] for r in group),
            "failed_or_incomplete":sum(r["failure_or_incomplete"] for r in group),
            "trace_request_hashes":len({r["request_sha256"] for r in group}),
            "role_evidence":role_evidence,
            "failure_classes":sorted({str(r.get("http_error_class",r.get("transport_error")))
                                      for r in failures}),
            "shadow_promotion":"RESEARCH_CANDIDATE_ONLY" if
                any(r["objective_pass"] for r in group) else "HOLD_NO_QUALIFIED_OUTPUT",
            "production_admitted":False,
            "dispatch_authorized":False,
            "account_lease_verified":False,
            "account_quota_authoritative":False,
            "billing_fallback_guard_verified":False,
            "privacy_policy_assessed_for_real_data":False,
        }
        models.append(audit)
    return {
        "schema":SCHEMA,
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "input_receipt_count":len(receipts),"products":sorted({r["product"] for r in receipts}),
        "model_product_pairs":len(models),
        "record_types":{"http_success":sum(r["http_status"]==200 for r in receipts),
                        "objective_success":sum(r["objective_pass"] for r in receipts),
                        "network_or_http_failures":sum(r["http_status"]!=200 for r in receipts)},
        "models":models,"all_dispatch_denied":all(not m["dispatch_authorized"] for m in models),
        "authoritative_routing":False,
        "limitations":"Observed synthetic behavior only. Shared upstream quota lease, privacy, account entitlement and complete billing receipts unproven. No production route admission; no hidden weights or internal model trace.",
    }

def output_exclusive(file: Path, data: object):
    raw=stable(data)
    fd=os.open(file,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return sha(raw)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",default=str(ROOT),help="External research receipt directory")
    p.add_argument("--out",required=True,help="New deny-only output JSON path")
    args=p.parse_args()
    observations=collect(Path(args.root))
    result=project(observations)
    digest=output_exclusive(Path(args.out),result)
    print(json.dumps({"path":args.out,"sha256":digest,
        "receipts":len(observations),"pairs":result["model_product_pairs"],
        "objective_passes":result["record_types"]["objective_success"],
        "all_dispatch_denied":result["all_dispatch_denied"]},sort_keys=True))

if __name__=="__main__": main()
