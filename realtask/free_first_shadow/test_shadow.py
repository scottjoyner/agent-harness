"""Self-contained offline tests; no provider credentials, network, or fleet services."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from realtask.free_first_shadow import capability_evidence as ev
from realtask.free_first_shadow import role_plan as plan


def synthetic_projection():
    pairs={}
    for _,(product,model,role,_) in plan.ROLE_RECIPES.items():
        m=pairs.setdefault((product,model),{
            "product":product,"model":model,"role_evidence":{},
            "production_admitted":False,"dispatch_authorized":False})
        m["role_evidence"][role]={
            "attempts":1,"objective_passes":1,"output_digests":["a"*64]}
    return {"schema":"free-model-capability-shadow/v1",
            "model_product_pairs":len(pairs),"models":list(pairs.values())}


class FreeProviderShadowTests(unittest.TestCase):
    def test_model_identity_separated_by_product(self):
        self.assertEqual(ev.canonical_model('smoke',{"provider":"openrouter",
            "exact_model_id":"cohere/north-mini-code:free"}),
            ("OpenRouter","cohere/north-mini-code:free"))
        self.assertEqual(ev.canonical_model('smoke',{"provider":"cohere",
            "exact_model_id":"north-mini-code-1-0"}),
            ("Cohere DIRECT","north-mini-code-1-0"))

    def test_json_scoring_not_name_only(self):
        self.assertTrue(ev.score_json('{"values":[1,3,5],"sum":9}'))
        self.assertFalse(ev.score_json('{"values":[5,3,1],"sum":9}'))

    def test_policy_review_objective(self):
        self.assertTrue(ev.scored_role('quota-auditor',
            '{"admitted":false,"maximum_shared_requests":50}'))
        self.assertFalse(ev.scored_role('quota-auditor',
            '{"admitted":true,"maximum_shared_requests":100}'))

    def make_receipt(self,root,served='example/model:free',cost=0):
        path=root/'quota-auditor.json'
        answer='{"admitted":false,"maximum_shared_requests":50}'
        obj={"provider":"kilo","exact_model_id":"example/model:free",
             "role":"quota-auditor","request_sha256":"c"*64,
             "timestamp_utc":"2026-10-22T15:00:00Z","http_status":200,
             "returned_model":served,"reported_cost":cost,
             "output_chars":len(answer),
             "output_sha256":ev.sha(answer.encode()),"usage":{"completion_tokens":12}}
        path.write_text(json.dumps(obj))
        (root/'quota-auditor.synthetic.txt').write_text(answer)
        return path

    def test_full_receipt_exact_attribution_and_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            row=ev.verify_receipt('role',self.make_receipt(root),root)
            self.assertTrue(row['objective_pass'])
            self.assertTrue(row['served_model_exact'])
            self.assertTrue(row['reported_zero_cost'])
            self.assertFalse(row['independent_secondary_test_passed'])

    def test_wrong_returned_model_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            row=ev.verify_receipt('role',
                self.make_receipt(root,served='other/model:free'),root)
            self.assertFalse(row['objective_pass'])
            self.assertTrue(row['served_model_conflict'])

    def test_unknown_money_does_not_become_free(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            row=ev.verify_receipt('role',
                self.make_receipt(root,cost="NOT_RETURNED"),root)
            self.assertTrue(row['monetary_cost_unverified'])
            self.assertFalse(row['reported_zero_cost'])

    def test_tampered_output_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            path=self.make_receipt(root)
            (root/'quota-auditor.synthetic.txt').write_text('tampered')
            with self.assertRaises(ValueError):
                ev.verify_receipt('role',path,root)

    def test_no_production_admission_from_successes(self):
        row={"product":"Kilo anonymous","exact_model_id":"example/model:free",
             "role":"quota-auditor","http_status":200,
             "objective_pass":True,"reported_zero_cost":True,
             "monetary_cost_unverified":False,"failure_or_incomplete":False,
             "request_sha256":"a"*64,"receipt_sha256":"b"*64,
             "shared_quota_group":"UNKNOWN"}
        result=ev.project([row])
        self.assertTrue(result['all_dispatch_denied'])
        self.assertFalse(result['models'][0]['dispatch_authorized'])
        self.assertFalse(result['models'][0]['account_lease_verified'])

    def test_role_projection_only_if_evidenced(self):
        result=plan.build(synthetic_projection())
        self.assertEqual(len(result["roles"]),5)
        self.assertFalse(result["fleet_dispatch_enabled"])
        self.assertTrue(all(not x["dispatch_allowed"] for x in result["roles"].values()))

    def test_role_admission_lie_fails_closed(self):
        data=synthetic_projection()
        data['models'][0]['dispatch_authorized']=True
        with self.assertRaisesRegex(ValueError,'admission'):
            plan.build(data)

    def test_missing_role_pass_is_not_recommended(self):
        data=synthetic_projection()
        first=next(iter(data['models']))
        first['role_evidence'][next(iter(first['role_evidence']))]['objective_passes']=0
        with self.assertRaisesRegex(ValueError,'qualified'):
            plan.build(data)

    def test_refuse_unrecognized_projection_schema(self):
        bad=synthetic_projection()
        bad["schema"]="unknown"
        with self.assertRaisesRegex(ValueError,'schema'):
            plan.build(bad)


if __name__=="__main__": unittest.main()
