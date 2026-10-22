"""Negative release controls for an isolated, real Hermes *image* canary."""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import assistx_hermes_container_canary as c


class ContainedHermesImageCanaryTests(unittest.TestCase):
    def setUp(self):
        self.id="sha256:"+"a"*64
        self.root_ctx=tempfile.TemporaryDirectory()
        self.addCleanup(self.root_ctx.cleanup)
        self.parent=Path(self.root_ctx.name)
        self.path=self.parent/"og-hermes-containment-Aa_09Zx1"
        self.path.mkdir()
        self.knowledge=self.path/"knowledge"
        self.work=self.path/"worktree"
        self.knowledge.mkdir()
        self.work.mkdir()
        self.name="og-hermes-image-canary-"+"b"*20

    def argv(self):
        with patch.object(c,"SCRATCH_PARENT",self.parent):
            return c.docker_create_argv(
                self.id,self.name,self.knowledge,self.work)

    def test_one_exact_synthetic_image_command_cannot_run_agent(self):
        command=self.argv()
        self.assertEqual(command[0:2],["docker","create"])
        self.assertIn("--network=none",command)
        self.assertIn("--read-only",command)
        self.assertIn("--cap-drop=ALL",command)
        self.assertIn("--security-opt=no-new-privileges:true",command)
        self.assertIn("--entrypoint=/bin/sh",command)
        self.assertIn(f"--user={os.getuid()}:{os.getgid()}",command)
        self.assertIn("--pull=never",command)
        self.assertIn("--memory=256m",command)
        self.assertIn(self.id,command)
        self.assertIn("SYNTHETIC_ONLY",command[-1])
        self.assertNotIn("assistx.strict_executor_main"," ".join(command))
        # The script deliberately mentions protected paths ONLY in
        # negative assertions. None is passed as an actual Docker mount.
        sources=" ".join(
            command[i+1] for i,k in enumerate(command[:-1])
            if k=="--mount")
        for forbidden in (
            "docker.sock","/media/scott/SSD_4TB","/media/scott/NAS5",
            "/home/scott/.ssh","/home/scott/knowledge"):
            self.assertNotIn(forbidden,sources)

    def test_only_two_expected_synthetic_mounts(self):
        command=self.argv()
        mounts=[command[i+1] for i,k in enumerate(command[:-1])
                if k=="--mount"]
        self.assertEqual(len(mounts),2)
        self.assertEqual(
            mounts[0],
            f"type=bind,src={self.knowledge},dst=/research/knowledge,readonly")
        self.assertEqual(
            mounts[1],
            f"type=bind,src={self.work},dst=/workspace")

    def test_wrong_source_or_non_sha256_image_denied(self):
        for bad in ("latest","auto-assist-hermes-adapter:latest",
                    "sha256:"+"A"*64,"sha256:"+"0"*32):
            with self.subTest(id=bad):
                with patch.object(c,"SCRATCH_PARENT",self.parent):
                    with self.assertRaises(c.UnsafeCanary):
                        c.docker_create_argv(bad,self.name,
                                             self.knowledge,self.work)

    def test_refuses_real_production_mount_sources(self):
        with patch.object(c,"SCRATCH_PARENT",self.parent):
            with self.assertRaises(c.UnsafeCanary):
                c.docker_create_argv(
                    self.id,self.name,Path("/home/scott/knowledge"),self.work)
            with self.assertRaises(c.UnsafeCanary):
                c.docker_create_argv(
                    self.id,self.name,self.knowledge,
                    Path("/media/scott/SSD_4TB"))

    def test_rejects_wrong_container_name_and_path_escape(self):
        for name in ("assistx-hermes-adapter",
                     "og-hermes-image-canary-"+"/"*20,
                     "og-hermes-image-canary-"+"f"*19):
            with self.subTest(name=name):
                with patch.object(c,"SCRATCH_PARENT",self.parent):
                    with self.assertRaises(c.UnsafeCanary):
                        c.docker_create_argv(
                            self.id,name,self.knowledge,self.work)
        rogue=self.parent/"other-root"/"knowledge"
        rogue.parent.mkdir()
        rogue.mkdir()
        with patch.object(c,"SCRATCH_PARENT",self.parent):
            with self.assertRaises(c.UnsafeCanary):
                c.docker_create_argv(
                    self.id,self.name,rogue,self.work)

    def test_untrusted_symlink_source_denied(self):
        alias=self.path/"symlink-knowledge"
        alias.symlink_to(self.knowledge,target_is_directory=True)
        with patch.object(c,"SCRATCH_PARENT",self.parent):
            with self.assertRaises(c.UnsafeCanary):
                c.docker_create_argv(self.id,self.name,alias,self.work)

    def test_disallows_root_host_credential_boundary(self):
        with patch.object(c,"SCRATCH_PARENT",self.parent), \
             patch("assistx_hermes_container_canary.os.getuid",return_value=0):
            with self.assertRaises(c.UnsafeCanary):
                c.docker_create_argv(
                    self.id,self.name,self.knowledge,self.work)

    def test_no_physical_execution_dry_run_stays_hold(self):
        report=c.capture(enable_physical=False)
        self.assertTrue(c.verify(report))
        self.assertFalse(report["physical_canary_executed"])
        self.assertFalse(report["agent_dispatch_authorized"])
        self.assertFalse(report["production_deployment_authorized"])
        self.assertFalse(report["hermes_workflow_compatibility_verified"])
        self.assertEqual(report["decision"],"HOLD")

    def test_synthetic_full_containment_still_not_prod_grant(self):
        report=c.capture(enable_physical=False)
        report.update({k:True for k in c.REQUIRED_CHECKS})
        report["physical_canary_executed"]=True
        report["exact_running_image_object_sha256"]=self.id
        self.assertTrue(c.verify(report))
        self.assertFalse(report["agent_dispatch_authorized"])
        self.assertFalse(report["physical_production_evidence_write_denial_verified"])
        self.assertFalse(report["hermes_workflow_compatibility_verified"])
        self.assertFalse(report["production_deployment_authorized"])

    def test_missing_evidence_not_validated_as_physical_pass(self):
        report=c.capture(enable_physical=False)
        report["physical_canary_executed"]=True
        self.assertFalse(c.verify(report))
        report["physical_canary_executed"]=False
        report["one_shot_container_removed"]=True
        self.assertFalse(c.verify(report))

    def test_forged_positive_authorities_never_validated(self):
        baseline=c.capture(enable_physical=False)
        for key in c.ABSENT_AUTHORITIES:
            with self.subTest(key=key):
                record=copy.deepcopy(baseline)
                record[key]=True
                self.assertFalse(c.verify(record))

    def test_wrong_provider_count_and_wrong_bool_denied(self):
        baseline=c.capture(enable_physical=False)
        for value in (-1,1,True,"0"):
            with self.subTest(value=value):
                self.assertFalse(c.verify({
                    **baseline,"provider_requests_forwarded":value}))

    def test_image_id_source_must_be_real_running_container(self):
        with patch.object(c,"_exec",return_value=CompletedProcess(
                args=[],returncode=0,stdout=self.id+"\n",stderr="")) as fake:
            self.assertEqual(c._image_id(),self.id)
        self.assertEqual(fake.call_args.args[0],[
            "docker","inspect","--format","{{.Image}}",
            "assistx-hermes-adapter"])

    def test_bad_image_pinning_fails_closed(self):
        for value in (
            "latest\n","sha256:"+"Z"*64+"\n",
            "sha256:"+"a"*32+"\n",
        ):
            with patch.object(c,"_exec",return_value=CompletedProcess(
                    args=[],returncode=0,stdout=value,stderr="")):
                with self.assertRaises(c.UnsafeCanary):
                    c._image_id()

    def test_docker_failure_does_not_claim_container_verified(self):
        report=c.capture(enable_physical=False)
        with patch.object(c,"_image_id",side_effect=c.UnsafeCanary(
                "unavailable")),patch(
                "assistx_hermes_container_canary.os.uname") as uname:
            uname.return_value.nodename="x1-370"
            observed=c.capture(enable_physical=True)
        self.assertTrue(c.verify(observed))
        self.assertFalse(observed["physical_canary_executed"])
        self.assertIn("synthetic_container_canary_failed_closed",
                      observed["blockers"])

    def test_wrong_host_never_inspects_production_docker(self):
        with patch("assistx_hermes_container_canary.os.uname") as uname, \
             patch.object(c,"_image_id") as imageid:
            uname.return_value.nodename="beelink"
            record=c.capture(enable_physical=True)
        imageid.assert_not_called()
        self.assertTrue(c.verify(record))
        self.assertFalse(record["physical_canary_executed"])

    def test_non_production_scope_remains_in_fail_closed_blockers(self):
        report=c.capture(enable_physical=False)
        self.assertIn("hermes_agent_entrypoint_and_workflow_not_executed",
                      report["blockers"])
        self.assertIn("source_image_git_provenance_unverified",
                      report["blockers"])
        self.assertIn("source_owner_attestation_revocation_unverified",
                      report["blockers"])

    def test_physical_shell_has_no_network_or_credential_commands(self):
        script=c.SHELL_SCRIPT
        self.assertIn("NoNewPrivs",script)
        self.assertIn("CapEff",script)
        self.assertIn("SYNTHETIC_ONLY",script)
        for unsafe in ("curl ","wget ","ssh ","token","/dev/tcp/",
                       "python -m assistx", "strict_executor_main"):
            self.assertNotIn(unsafe,script)


if __name__=="__main__":
    unittest.main()
