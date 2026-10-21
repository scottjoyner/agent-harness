"""Assume no credentials and no dispatch for ANY AssistX/Router observation."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from assistx_readonly_bridge import (
    ALL_UNVERIFIED, MAX_MODELS, URLS, UnsafeBridgeEvidence,
    _check_identity, _inventory_count, _parse_json,
    _request_status_or_json, capture, verify,
)
from assistx_operator_probe import PATHS, PROCESSES


def assistx_openapi():
    return {
        "info":{"title":"AssistX API & UI"},
        "components":{"securitySchemes":{"HTTPBasic":{"type":"http","scheme":"basic"}}},
        "paths":{
            "/health":{"get":{}},
            "/api/v1/auth/whoami":{"get":{"security":[{"HTTPBasic":[]}] }},
            "/api/v1/runtime/catalog":{"get":{"security":[{"HTTPBasic":[]}] }},
        }
    }


def router_openapi():
    return {"info":{"title":"auto-router"},
            "paths":{"/health":{"get":{}},"/v1/models":{"get":{}}}}


def data(count=4):
    return {"object":"list","data":[{"id":"synthetic-model-"+str(i),"object":"model"}
                                    for i in range(count)]}


def host_record():
    return {
        "schema":1,"mode":"assistx_host_local_readonly_deployment_probe",
        "process_presence":{k:True for k in PROCESSES},
        "host_local_health_http_status":{k:200 for k in PATHS},
        "tailnet_serve_backend_port":8000,
        "read_only_http_get_only":True,
        "authenticated_catalog_verified":False,
        "exact_runtime_image_verified":False,
        "source_authoritative_transactions_verified":False,
        "independent_admission_verified":False,
        "production_deployment_authorized":False,
        "agent_dispatch_authorized":False,
        "provider_requests_forwarded":0,
        "shell_executor_authorized":False,
        "automatic_retry_or_refund_authorized":False,
        "decision":"HOLD",
        "blockers":[
            "authenticated_assistx_catalog_not_verified",
            "exact_deployed_image_and_commit_not_verified",
            "source_owner_bound_execution_not_verified",
            "independent_admission_and_revocation_custody_not_verified"]
    }


RESPONSES={
    "assistx_openapi":(200,assistx_openapi()),
    "router_openapi":(200,router_openapi()),
    "assistx_auth":(401,None),
    "assistx_catalog":(401,None),
    "router_inventory":(200,data()),
}


class AssistXReadonlyBridgeTests(unittest.TestCase):
    def fixture(self, *, response_overrides=None, host_overrides=None,
                host_valid=True):
        responses={**RESPONSES,**(response_overrides or {})}
        host={**host_record(),**(host_overrides or {})}
        with (
            patch("assistx_readonly_bridge.observe_host",return_value=host),
            patch("assistx_readonly_bridge.validate_host",return_value=host_valid),
            patch("assistx_readonly_bridge._request_status_or_json",
                  side_effect=lambda k:responses[k]),
        ):
            return capture()

    def test_healthy_services_and_visible_models_never_admit(self):
        result=self.fixture()
        self.assertTrue(verify(result))
        self.assertEqual(result["router_inventory"]["advertised_model_count"],4)
        self.assertTrue(result["service_identity"]["assistx_openapi_title_verified"])
        self.assertTrue(result["service_identity"]["router_openapi_title_verified"])
        self.assertTrue(result["uncredentialed_boundary"]["anonymous_denial_observed"])
        self.assertFalse(result["router_inventory"]["model_identifiers_exported"])
        self.assertEqual(result["decision"],"HOLD")
        self.assertEqual(result["provider_requests_forwarded"],0)
        for k in ALL_UNVERIFIED:self.assertIs(result[k],False)

    def test_assistx_catalog_200_without_credentials_is_security_failure(self):
        result=self.fixture(response_overrides={"assistx_catalog":(200,None)})
        self.assertFalse(result["uncredentialed_boundary"]["anonymous_denial_observed"])
        self.assertIn("protected_assistx_routes_not_denying_anonymous",
                      result["blockers"])
        self.assertFalse(result["agent_dispatch_authorized"])

    def test_identity_gets_200_without_credentials_is_security_failure(self):
        result=self.fixture(response_overrides={"assistx_auth":(200,None)})
        self.assertFalse(result["uncredentialed_boundary"]["anonymous_denial_observed"])
        self.assertFalse(result["source_owner_bound_execution_verified"])

    def test_protected_routes_403_are_denied_as_expected(self):
        result=self.fixture(response_overrides={
            "assistx_auth":(403,None), "assistx_catalog":(403,None)})
        self.assertTrue(verify(result))
        self.assertTrue(result["uncredentialed_boundary"]["anonymous_denial_observed"])
        self.assertFalse(result["authenticated_catalog_access_verified"])

    def test_network_unreachable_stays_hold(self):
        overrides={key:(None,None) for key in RESPONSES}
        result=self.fixture(response_overrides=overrides)
        self.assertTrue(verify(result))
        self.assertIsNone(result["router_inventory"]["advertised_model_count"])
        self.assertFalse(result["service_identity"]["router_openapi_title_verified"])
        self.assertFalse(result["uncredentialed_boundary"]["anonymous_denial_observed"])

    def test_false_assistx_or_router_identity_not_accepted(self):
        for key,title in (("assistx_openapi","unknown-api"),("router_openapi","wrong-router")):
            bad=assistx_openapi() if key=="assistx_openapi" else router_openapi()
            bad["info"]["title"]=title
            with self.subTest(key=key):
                result=self.fixture(response_overrides={key:(200,bad)})
                self.assertFalse(all(result["service_identity"].values()))
                self.assertEqual(result["decision"],"HOLD")

    def test_wrong_openapi_auth_contract_not_verified(self):
        for broken in (
            {"paths":{}, "info":{"title":"AssistX API & UI"}},
            {"paths":assistx_openapi()["paths"],"info":{"title":"AssistX API & UI"}},
        ):
            result=self.fixture(response_overrides={"assistx_openapi":(200,broken)})
            self.assertFalse(result["service_identity"]["assistx_openapi_title_verified"])

    def test_wrong_auth_method_on_openapi_not_verified(self):
        doc=assistx_openapi()
        doc["paths"]["/api/v1/runtime/catalog"]["get"]["security"]=[]
        result=self.fixture(response_overrides={"assistx_openapi":(200,doc)})
        self.assertFalse(result["service_identity"]["assistx_openapi_title_verified"])

    def test_empty_router_model_inventory_is_not_admission(self):
        result=self.fixture(response_overrides={"router_inventory":(200,data(0))})
        self.assertTrue(verify(result))
        self.assertEqual(result["router_inventory"]["advertised_model_count"],0)
        self.assertFalse(result["loaded_model_residency_verified"])

    def test_incorrect_model_shape_is_missing_not_zero(self):
        for bad in (
            {"data":[]},
            {"object":"list","data":None},
            {"object":"list","data":[{"id":"","object":"model"}]},
            {"object":"list","data":[{"id":"x","object":"unauthorized"}]},
            {"object":"list","data":[{"id":"x"*300,"object":"model"}]},
            {"object":"list","data":[{"id":"x","object":"model"}]*(MAX_MODELS+1)},
        ):
            with self.subTest(example=str(bad)[:45]):
                self.assertIsNone(_inventory_count(bad))

    def test_schema_auth_flags_cannot_be_changed(self):
        report=self.fixture()
        for key in ALL_UNVERIFIED:
            with self.subTest(flag=key):
                self.assertFalse(verify({**report,key:True}))

    def test_provider_forward_count_as_bool_or_nonzero_is_denied(self):
        report=self.fixture()
        for value in (True,1,-1):
            self.assertFalse(verify({**report,"provider_requests_forwarded":value}))

    def test_no_model_ids_or_urls_appear_in_output(self):
        report=self.fixture()
        serialized=json.dumps(report)
        self.assertNotIn("synthetic-model-",serialized)
        self.assertNotIn("http://127.",serialized)
        self.assertFalse(report["router_inventory"]["model_identifiers_exported"])

    def test_bad_json_and_duplicate_keys_denied(self):
        for bad in (b'{"x":1,"x":2}',b'[]',b'{"abc":',b'\xff',b'{}'*999):
            with self.subTest(bad=bad[:25]):
                with self.assertRaises(UnsafeBridgeEvidence):
                    _parse_json(bad,500)

    def test_invalid_size_or_nonbytes_denied(self):
        with self.assertRaises(UnsafeBridgeEvidence):
            _parse_json(b'{"x":1}',3)
        with self.assertRaises(UnsafeBridgeEvidence):
            _parse_json("not bytes",100)

    def test_transport_accepts_only_fixed_urls_and_methods(self):
        with self.assertRaises(UnsafeBridgeEvidence):
            _request_status_or_json("http://127.0.0.1:8000/api/v1/model/chat/completions")

    def test_unhealthy_host_does_not_become_admitted(self):
        report=self.fixture(
            host_valid=False,
            host_overrides={"host_local_health_http_status":
                            {k:None for k in PATHS}})
        self.assertTrue(verify(report))
        self.assertFalse(report["host_observation_valid"])
        self.assertFalse(report["production_deployment_authorized"])

    def test_noncanonical_host_health_report_does_not_get_positive_gate(self):
        report=self.fixture(host_valid=False,
                            host_overrides={"host_local_health_http_status":{}})
        self.assertTrue(verify(report))
        self.assertIsNone(report["host_unauthenticated_health"])

    def test_tampered_result_cannot_claim_dispatch(self):
        report=self.fixture()
        self.assertFalse(verify({**report,"decision":"GO"}))
        self.assertFalse(verify({**report,"router_inventory":{
            **report["router_inventory"],
            "model_identifiers_exported":True}}))

    def test_required_blockers_cannot_be_deleted(self):
        report=self.fixture()
        self.assertFalse(verify({**report,"blockers":[]}))
        self.assertFalse(verify({**report,"schema":True}))

    def test_openapi_static_title_contract_is_explicit(self):
        self.assertTrue(_check_identity(
            assistx_openapi(), title="AssistX API & UI",
            required_methods={
                "/health":"get",
                "/api/v1/auth/whoami":"get",
                "/api/v1/runtime/catalog":"get"},
            protected_get=(
                "/api/v1/auth/whoami",
                "/api/v1/runtime/catalog")))
        self.assertFalse(_check_identity(
            assistx_openapi(), title="Different service",
            required_methods={"/health":"get"}))


if __name__ == "__main__":
    unittest.main()
