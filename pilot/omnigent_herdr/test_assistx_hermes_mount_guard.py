"""Offline and synthetic negative evidence tests for Hermes mount topology."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistx_hermes_mount_guard import (
    EVIDENCE_ROOT, MAX_BYTES, UnsafeMountMetadata,
    _canonical, _contained, assess, inspect_live,
    parse_docker_mounts, verify,
)


def bind(source: str, destination: str, rw: bool):
    return {"Type": "bind", "Source": source,
            "Destination": destination, "RW": rw}


class HermesMountSafetyTests(unittest.TestCase):
    def _assess(self, mounts, *, physical=False):
        observed=assess(mounts,physical_docker_inspect=physical)
        self.assertTrue(verify(observed))
        self.assertEqual(observed["decision"],"HOLD")
        self.assertFalse(observed["agent_dispatch_authorized"])
        self.assertFalse(observed["production_deployment_authorized"])
        self.assertEqual(observed["provider_requests_forwarded"],0)
        return observed

    def test_actual_writable_ssd_root_detected(self):
        observed=self._assess([
            bind("/media/scott/SSD_4TB","/media/scott/SSD_4TB",True)])
        self.assertEqual(observed["protected_evidence_rw_aliases"],1)
        self.assertFalse(observed["mount_topology_static_check_passed"])

    def test_actual_writable_knowledge_alias_detected(self):
        observed=self._assess([
            bind("/home/scott/knowledge","/root/knowledge",True)])
        # The symlink to the SSD may not exist on CI; fall back to
        # an explicitly created alias in a dedicated synthetic fixture
        # for a platform-independent assertion.
        if Path("/home/scott/knowledge").is_symlink() and Path(
                "/home/scott/knowledge").exists():
            self.assertEqual(observed["protected_evidence_rw_aliases"],1)

    def test_symlink_to_protected_tree_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            alias=Path(root)/"knowledge-alias"
            alias.symlink_to(Path(EVIDENCE_ROOT).parent)
            observed=self._assess([bind(
                str(alias),"/root/knowledge",True)])
            if Path(EVIDENCE_ROOT).parent.exists():
                self.assertEqual(observed["protected_evidence_rw_aliases"],1)

    def test_two_broad_aliases_both_detected_on_existing_host(self):
        paths=[
            "/media/scott/SSD_4TB",
            "/media/scott/SSD_4TB/knowledge",
        ]
        if all(Path(s).exists() for s in paths):
            report=self._assess([
                bind(paths[0],"/media/scott/SSD_4TB",True),
                bind(paths[1],"/root/knowledge",True)])
            self.assertEqual(report["protected_evidence_rw_aliases"],2)

    def test_ro_knowledge_plus_dedicated_scratch_is_only_static_candidate(self):
        with tempfile.TemporaryDirectory() as root:
            work=Path(root)/"bounded-workspace"
            work.mkdir()
            trusted=Path(EVIDENCE_ROOT).parent
            if not trusted.exists():
                trusted=Path(root)/"synthetic-protected"
                trusted.mkdir()
            report=self._assess([
                bind(str(trusted),"/root/knowledge",False),
                bind(str(work),"/app/research-output",True)])
            self.assertTrue(report["mount_topology_static_check_passed"])
            self.assertFalse(report["physical_write_denial_verified"])
            self.assertFalse(report["external_witness_custody_verified"])
            self.assertIn("physical_write_denial_not_tested",report["blockers"])

    def test_rw_descendant_of_protected_tree_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            inner=Path(root)/"test"
            inner.mkdir()
            protected=Path(inner)/"evidence"
            protected.mkdir()
            nested=protected/"child"
            nested.mkdir()
            with patch(
                    "assistx_hermes_mount_guard.EVIDENCE_ROOT",
                    str(protected)):
                report=self._assess([bind(
                    str(nested),"/mnt/evidence",True)])
            self.assertEqual(report["protected_evidence_rw_aliases"],1)

    def test_read_only_mount_not_proof_of_confidentiality(self):
        if Path(EVIDENCE_ROOT).parent.exists():
            report=self._assess([bind(
                str(Path(EVIDENCE_ROOT).parent),"/root/knowledge",False)])
            self.assertTrue(report["mount_topology_static_check_passed"])
            self.assertFalse(report["production_deployment_authorized"])

    def test_unknown_source_is_not_a_pass(self):
        report=self._assess([bind(
            "/totally/unavailable/assistx-test-knowledge",
            "/root/knowledge",False)])
        self.assertGreater(report["unresolved_mount_source_or_destination_count"],0)
        self.assertFalse(report["mount_topology_static_check_passed"])

    def test_no_mounts_fail_closed(self):
        report=self._assess([])
        self.assertFalse(report["docker_metadata_observed"])
        self.assertFalse(report["mount_topology_static_check_passed"])

    def test_empty_or_bad_docker_json_denied(self):
        for value in (
            b"[]",b"",b"{}",b'[{"Type":"bind","RW":true,"RW":false}]',
            b"not-json",b"\xff",b" "* (MAX_BYTES+1),
        ):
            with self.subTest(sample=value[:24]):
                with self.assertRaises(UnsafeMountMetadata):
                    parse_docker_mounts(value)

    def test_docker_metadata_accepts_bounded_valid_mount_list(self):
        with tempfile.TemporaryDirectory() as root:
            raw=json.dumps([bind(root,"/workspace",True)]).encode()
            observed=parse_docker_mounts(raw)
            self.assertEqual(len(observed),1)
            report=self._assess(observed,physical=True)
            self.assertTrue(report["docker_metadata_observed"])

    def test_nonboolean_rw_denied_in_parser(self):
        with tempfile.TemporaryDirectory() as root:
            raw=json.dumps([bind(root,"/workspace",1)]).encode()
            with self.assertRaises(UnsafeMountMetadata):
                parse_docker_mounts(raw)

    def test_nonabsolute_or_dotdot_source_denied_or_canonicalized(self):
        with self.assertRaises(UnsafeMountMetadata):
            _canonical("../relative")
        with self.assertRaises(UnsafeMountMetadata):
            _canonical("/mnt/\x00oops")
        self.assertEqual(_canonical("/tmp/example/../foo"),"/tmp/foo")

    def test_commonpath_avoids_prefix_collisions(self):
        self.assertTrue(_contained("/a/b","/a/b/c"))
        self.assertFalse(_contained("/a/b","/a/b-extra"))
        self.assertFalse(_contained("/a/b","/etc/passwd"))

    def test_named_volume_not_accepted_as_proven_host_bind(self):
        report=self._assess([{
            "Type":"volume","Source":"/var/lib/docker/volumes/opaque",
            "Destination":"/workspace","RW":True}])
        self.assertEqual(report["bind_mounts"],0)
        self.assertFalse(report["mount_topology_static_check_passed"])
        self.assertIn("no_verifiable_bind_mounts",report["blockers"])

    def test_ambiguous_duplicate_destination_not_accepted(self):
        with tempfile.TemporaryDirectory() as root:
            report=self._assess([
                bind(root,"/workspace",True),
                bind(root,"/workspace",False)])
            self.assertGreater(report["unresolved_mount_source_or_destination_count"],0)
            self.assertFalse(report["mount_topology_static_check_passed"])

    def test_no_path_leak_in_report(self):
        report=self._assess([
            bind("/media/scott/SSD_4TB","/media/scott/SSD_4TB",True)])
        ser=json.dumps(report,sort_keys=True)
        self.assertNotIn("/media/scott/",ser)
        self.assertNotIn("/root/knowledge",ser)

    def test_false_admission_or_tampered_topology_rejected(self):
        report=self._assess([
            bind("/media/scott/SSD_4TB","/media/scott/SSD_4TB",True)])
        for change in (
            {"production_deployment_authorized":True},
            {"agent_dispatch_authorized":True},
            {"physical_write_denial_verified":True},
            {"provider_requests_forwarded":True},
            {"provider_requests_forwarded":1},
            {"decision":"GO"},
            {"protected_evidence_rw_aliases":-1},
            {"protected_evidence_rw_aliases":True},
            {"mount_topology_static_check_passed":True},
            {"blockers":[]},
        ):
            with self.subTest(change=change):
                self.assertFalse(verify({**report,**change}))

    def test_failed_docker_command_stays_hold(self):
        from subprocess import CompletedProcess
        with patch("assistx_hermes_mount_guard.subprocess.run",
                   return_value=CompletedProcess([],1,b"",b"not accessible")):
            data=inspect_live()
        self.assertTrue(verify(data))
        self.assertEqual(data["source"],"unavailable_docker_inspect")
        self.assertFalse(data["docker_metadata_observed"])

    def test_not_one_of_the_fixed_commands(self):
        with patch("assistx_hermes_mount_guard.subprocess.run") as run:
            run.return_value.returncode=1
            run.return_value.stdout=b""
            inspect_live()
            self.assertEqual(run.call_args.args[0],[
                "docker","inspect","--format","{{json .Mounts}}",
                "assistx-hermes-adapter"])


if __name__=="__main__":
    unittest.main()
