#!/usr/bin/env python3
"""Derive role specialization candidates, never an executable provider route."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SCHEMA='fleet-free-agent-role-shadow/v1'
ROLE_RECIPES={
  "bounded_python_function_generation":(
      "Cohere DIRECT","north-mini-code-1-0","coder-cohere-v2",
      "20/20 independent held-out tests and 12/12 model-generated tests in no-network sandbox",
  ),
  "synthetic_test_case_design":(
      "Kilo anonymous","nvidia/nemotron-3-ultra-550b-a55b:free",
      "test-designer-recovery","12/12 interval cases validated by independent reference",
  ),
  "quota_and_shared_upstream_review":(
      "Kilo anonymous","nvidia/nemotron-3-ultra-550b-a55b:free",
      "quota-auditor","rejected double-counted 50-request shared quota in synthetic test",
  ),
  "safety_policy_review":(
      "Kilo anonymous","poolside/laguna-s-2.1:free",
      "security-auditor","rejected auto paid fallback/recharge in synthetic test",
  ),
  "targeted_python_patch_review":(
      "Cohere DIRECT","north-mini-code-1-0","patch-reviewer",
      "produced specific correction to synthetic off-by-one defect",
  ),
}
BLOCKERS=(
   "Provider-specific independent current zero-cost entitlement and remaining quota",
   "Shared-upstream lease ownership, fencing, reset and revocation",
   "Provider-account charging/automatic top-up and fallback protections",
   "Privacy/retention review for any non-synthetic fleet data",
   "Verified model identity and reproducible request-attribution receipts",
   "Physical fleet lane capacity and hardware co-residency proof",
   "Execution must occur only through separately authorized AssistX admission",
)
def sha(data: bytes)->str:
    return hashlib.sha256(data).hexdigest()

def build(source: dict)->dict:
    if source.get('schema')!='free-model-capability-shadow/v1':
        raise ValueError('unexpected source schema')
    models={(m['product'],m['model']):m for m in source['models']}
    entries={}
    for role,(product,model,task,description) in ROLE_RECIPES.items():
        m=models.get((product,model))
        if not m:raise ValueError('missing exact observed product/model: '+role)
        task_evidence=m['role_evidence'].get(task)
        if not task_evidence or task_evidence['objective_passes']<1:
            raise ValueError('no qualified observed outcome: '+role)
        if m.get('production_admitted') or m.get('dispatch_authorized'):
            raise ValueError('unexpected admission authority in research projection')
        entries[role]={
           'candidate_product':product,'candidate_exact_model_id':model,
           'matched_research_task':task,'tested_behavior':description,
           'observed_task_attempts':task_evidence['attempts'],
           'observed_task_passes':task_evidence['objective_passes'],
           'trace_receipt_sha256s':task_evidence['output_digests'],
           'capability_assurance':'task-specific synthetic research, limited sample',
           'dispatch_allowed':False,'can_retry_paid_model':False,
           'research_queue_only':True,'production_promoted':False,
           'fallback_allowed':False,'dynamic_routing_enabled':False,
           'next_gate':'Independent entitlement/quota/shared-lease + privacy & router authority',
        }
    if any(r['dispatch_allowed'] for r in entries.values()):
        raise AssertionError('deny-only plan violated')
    return {
        'schema':SCHEMA,'generated_utc':datetime.now(timezone.utc).isoformat(),
        'source_schema':source['schema'],
        'covered_research_product_model_pairs':source['model_product_pairs'],
        'roles':entries,'fleet_dispatch_enabled':False,
        'is_live_auto_router_provider_registry':False,
        'authoritative_shared_quota_lease':False,
        'required_gates':BLOCKERS,
        'excluded_or_blocked':{
           'OpenRouter':'Prior research sweep hit HTTP 429; stop, no retry/promotion',
           'OpenCode CONSOLE':'Flint earlier anonymous 403; entitlement/auto-recharge unknown',
           'OpenCode ZEN':'Separate product and unknown entitlement; no inherited Console pricing',
           'xAI DIRECT':'Tier 0 zero spend is not a verified free token grant',
           '9router OmniRoute Kilo aliases':'Do not multiply underlying provider quotas',
           'Joyner local R9700 Flint Q4_1':'4 existing inference servers + VRAM and swap constraints; local load HOLD'
        },
        'note':'A shortlist of tested synthetic behavior, NOT a production router or autonomous agent grant.'
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--projection',required=True)
    ap.add_argument('--out',required=True)
    a=ap.parse_args()
    sourcepath=Path(a.projection)
    data=json.loads(sourcepath.read_text())
    result=build(data)
    result['source_projection_sha256']=sha(sourcepath.read_bytes())
    p=Path(a.out)
    raw=(json.dumps(result,indent=2,sort_keys=True)+'\n').encode()
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw)
    print(json.dumps({'path':str(p),'roles':len(result['roles']),
                      'dispatch_allowed':result['fleet_dispatch_enabled'],
                      'sha256':sha(raw)},sort_keys=True))

if __name__=='__main__':main()
