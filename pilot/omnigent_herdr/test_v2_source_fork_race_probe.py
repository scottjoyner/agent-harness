"""Offline contract checks for a physical two-worker research race."""
import unittest
from v2_source_fork_race_probe import (
    SCENARIOS, DENIED, WITNESS, physical, result_code,
)


def fixture():
    return {
        "schema":1,"mode":"synthetic_two_worker_fenced_source_head_race",
        **{k:True for k in WITNESS},
        **{k:False for k in DENIED},
        "provider_requests_forwarded":0,
        "decision":"HOLD","errors":[],
    }


class ForkedResourceRaceContractTests(unittest.TestCase):
    def test_one_verified_synthetic_race_cannot_dispatch(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                data={**fixture(),"scenario":scenario}
                self.assertEqual(result_code(data),0)
                self.assertFalse(data["agent_dispatch_authorized"])
                self.assertFalse(data["automatic_retry_authorized"])

    def test_missing_any_fenced_evidence_denies(self):
        for witness in WITNESS:
            with self.subTest(witness=witness):
                self.assertEqual(result_code({**fixture(),witness:False}),2)

    def test_false_production_assurance_denies(self):
        for flag in DENIED:
            with self.subTest(flag=flag):
                self.assertEqual(result_code({**fixture(),flag:True}),2)

    def test_unsafe_forward_or_errors_denied(self):
        self.assertEqual(result_code({
            **fixture(),"provider_requests_forwarded":1}),2)
        self.assertEqual(result_code({
            **fixture(),"errors":["uncertainty"]}),2)

    def test_reject_other_source_or_bad_path_before_ssh(self):
        for remote,root in (
            ("lenovo","/home/scott/.cache/og-v2-source-Ab99Zz"),
            ("destroyer","/tmp/not-a-resource"),
            ("destroyer","/home/scott/.cache/og-v2-source-Ab99Zz/.."),
        ):
            with self.subTest(remote=remote,root=root):
                result=physical(remote_root=root,scenario=SCENARIOS[0],
                                host=remote)
                self.assertNotEqual(result_code(result),0)
                self.assertEqual(result["errors"],
                                 ["unapproved synthetic worker race"])


if __name__=="__main__":
    unittest.main()
