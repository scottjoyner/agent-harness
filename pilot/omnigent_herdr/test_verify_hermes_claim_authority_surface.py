"""No-service-I/O fixture tests for Hermes owner-claim API surface."""
from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import patch
import unittest

import verify_hermes_claim_authority_surface as scope


def example_sources():
    return {
        "api": '''
@app.get("/api/tasks")
def api_list_tasks(status: str = Query(None), limit: int = Query(50,le=500)):
    sql = "MATCH (t:Task) RETURN t LIMIT $limit"
    return {"items": [], "count": 0}
''',
        "claims": '''
def _mode():
    return os.getenv("ASSISTX_REQUIRE_WORKER_CLAIM_ID","modern").strip()
def _required_agents():
    return os.getenv("ASSISTX_CLAIM_REQUIRED_AGENTS","fleet-executor").split(",")
''',
        "hermes": '''
AGENT_ID = os.getenv("HERMES_AGENT_ID","hermes-local")
''',
        "database": '''
class Neo4jClient:
    def heartbeat_task(self,claim_id=None):
        return """AND ($claim_id IS NULL OR t.claim_id=$claim_id)"""
    def complete_task(self,claim_id=None):
        return """AND ($claim_id IS NULL OR t.claim_id=$claim_id)"""
''',
        "executor": '''
@router.get("/claims/{task_id}/status")
def claim_status(task_id: str):
    return {"active":False}
''',
    }


class SourceClaimSurfaceTests(unittest.TestCase):
    def check(self, files=None):
        return scope.analyze_sources(example_sources() if files is None else files)

    def test_current_risk_pattern_is_detected_and_release_stays_hold(self):
        report=self.check()
        self.assertEqual(report["observed"]["list_limit_cap"],500)
        self.assertTrue(report["observed"]["claim_status_is_per_task"])
        self.assertTrue(report["observed"]["db_heartbeat_permits_missing_claim_id"])
        self.assertTrue(report["observed"]["db_complete_permits_missing_claim_id"])
        self.assertIn("default_hermes_agent_not_required_to_present_claim_id",
                      report["violations"])
        self.assertIn("authoritative_claim_inventory_not_available_from_list_route",
                      report["violations"])
        self.assertFalse(report["production_deployment_authorized"])
        self.assertEqual(report["decision"],"HOLD")

    def test_exhaustive_snapshot_shape_reduces_listing_gap_but_not_grant_authority(self):
        files=example_sources()
        files["api"]='''
@app.get("/api/tasks")
def api_list_tasks(cursor: str=None, snapshot: str=None,
                   limit: int=Query(50,le=500)):
    return {"items":[], "count":0, "next_cursor":None,
            "snapshot_id":"fake", "total_count":0}
'''
        result=self.check(files)
        self.assertTrue(result["observed"]["list_has_cursor_input"])
        self.assertTrue(result["observed"]["list_has_complete_total"])
        self.assertNotIn("authoritative_claim_inventory_not_available_from_list_route",
                         result["violations"])
        self.assertFalse(result["real_claim_census_completeness_verified"])
        self.assertFalse(result["production_deployment_authorized"])

    def test_cursor_without_snapshot_still_denied(self):
        files=example_sources()
        files["api"]='''
@app.get("/api/tasks")
def api_list_tasks(cursor: str=None, limit: int=Query(50,le=500)):
    return {"items":[],"next_cursor":"next", "total_count":1000}
'''
        self.assertIn("authoritative_claim_inventory_not_available_from_list_route",
                      self.check(files)["violations"])

    def test_deleted_list_route_is_not_qualified(self):
        files=example_sources()
        files["api"]="def api_list_tasks(): return {'items': []}"
        self.assertIn("task_listing_route_unqualified",self.check(files)["violations"])

    def test_claim_status_only_proves_single_task_visibility(self):
        files=example_sources()
        files["executor"]="def claim_status(task_id): return {'active': True}"
        result=self.check(files)
        self.assertFalse(result["observed"]["claim_status_is_per_task"])
        self.assertIn("authoritative_claim_inventory_not_available_from_list_route",
                      result["violations"])

    def test_explicit_strict_all_default_removes_allowlist_gap(self):
        files=example_sources()
        files["claims"]=files["claims"].replace('"modern"','"all"')
        self.assertNotIn("default_hermes_agent_not_required_to_present_claim_id",
                         self.check(files)["violations"])

    def test_include_hermes_in_default_required_agents(self):
        files=example_sources()
        files["claims"]=files["claims"].replace(
            '"fleet-executor"', '"fleet-executor,hermes-local"')
        self.assertNotIn("default_hermes_agent_not_required_to_present_claim_id",
                         self.check(files)["violations"])

    def test_permissive_database_heartbeat_is_detected_independently(self):
        files=example_sources()
        files["database"]=files["database"].replace(
            "($claim_id IS NULL OR t.claim_id=$claim_id)",
            "t.claim_id=$claim_id",1)
        out=self.check(files)
        self.assertFalse(out["observed"]["db_heartbeat_permits_missing_claim_id"])
        self.assertTrue(out["observed"]["db_complete_permits_missing_claim_id"])
        self.assertIn("db_complete_permits_missing_claim_id",out["violations"])

    def test_legacy_execution_attempt_bypass_is_still_detected(self):
        files=example_sources()
        files["database"]=files["database"].replace(
            "t.claim_id=$claim_id", "coalesce(t.execution_attempt, 0)=0")
        result=self.check(files)
        self.assertTrue(result["observed"]["db_heartbeat_permits_missing_claim_id"])
        self.assertTrue(result["observed"]["db_complete_permits_missing_claim_id"])

    def test_missing_database_method_fails_closed(self):
        files=example_sources()
        files["database"]="class Neo4jClient: pass"
        out=self.check(files)
        self.assertIn("db_complete_permits_missing_claim_id",out["violations"])
        self.assertIn("db_heartbeat_permits_missing_claim_id",out["violations"])

    def test_missing_hermes_identity_is_not_assumed_safe(self):
        files=example_sources()
        files["hermes"]="AGENT_ID='unproven'"
        self.assertIn("hermes_identity_source_unqualified",self.check(files)["violations"])

    def test_invalid_or_oversized_source_never_valid(self):
        for files in (
            None, {}, {"api":"def x():"},
            {**example_sources(),"api":"def x(:"},
            {**example_sources(),"api":"x"*(scope.MAX_SOURCE_BYTES+1)},
        ):
            with self.subTest(case=str(files)[:24]):
                output=scope.analyze_sources(files)
                self.assertTrue(output["violations"])
                self.assertFalse(output["production_deployment_authorized"])

    def test_bool_or_bad_query_limit_is_not_treated_as_complete_snapshot(self):
        files=example_sources()
        files["api"]=files["api"].replace("le=500","le=True")
        report=self.check(files)
        self.assertIsNone(report["observed"]["list_limit_cap"])
        self.assertFalse(report["observed"]["list_has_snapshot_input"])
        self.assertFalse(report["real_claim_census_completeness_verified"])

    def test_nonoptin_default_never_reads_live_source(self):
        with patch.object(Path, "read_text") as reader:
            result=scope.analyze_sources(None)
        reader.assert_not_called()
        self.assertFalse(result["production_deployment_authorized"])
        self.assertFalse(result["real_claim_census_completeness_verified"])

    def test_report_does_not_leak_api_tokens_or_claim_payload(self):
        report=str(self.check())
        for string in ("FLEET_ROUTER_BEARER_TOKEN","/media/scott",
                       "SUPER_SECRET_HERMES_WORKER_KEY"):
            self.assertNotIn(string,report)

    def test_caller_cannot_add_extra_source_for_unapproved_probing(self):
        files=example_sources()
        files["/nas/Watchdog"]="untrusted"
        self.assertIn("source_inventory_missing",self.check(files)["violations"])


if __name__ == "__main__":
    unittest.main()
