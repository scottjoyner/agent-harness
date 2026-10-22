"""Offline fail-closed contract tests for hermes image normal-entrypoint canary."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import assistx_hermes_container_canary as shell
import assistx_hermes_normal_startup_canary as normal


TEST_IMAGE="sha256:"+"b"*64
SUFFIX="c"*16


class NormalHermesStartupSafety(unittest.TestCase):
    def test_dry_run_is_hold_and_does_not_call_docker(self):
        with patch.object(normal, "_call") as subprocess_mock:
            record=normal.capture(physical=False)
        subprocess_mock.assert_not_called()
        self.assertTrue(normal.validate(record))
        self.assertFalse(record["physical_canary_executed"])
        self.assertEqual(record["provider_requests_forwarded"],0)
        self.assertEqual(record["real_agent_tasks_executed"],0)
        self.assertEqual(record["decision"],"HOLD")

    def test_all_green_physical_evidence_stays_hold(self):
        result=normal.capture(physical=False)
        result.update({k:True for k in normal.TRUE_CHECKS})
        result["exact_running_base_image"]=TEST_IMAGE
        result["disposable_reproducible_overlay_image"]=TEST_IMAGE
        result["physical_canary_executed"]=True
        self.assertTrue(normal.validate(result))
        self.assertFalse(result["production_deployment_authorized"])
        self.assertFalse(result["agent_dispatch_authorized"])

    def test_not_even_one_failed_check_may_claim_physical_pass(self):
        result=normal.capture(physical=False)
        result.update({k:True for k in normal.TRUE_CHECKS})
        result["exact_running_base_image"]=TEST_IMAGE
        result["disposable_reproducible_overlay_image"]=TEST_IMAGE
        result["physical_canary_executed"]=True
        for key in normal.TRUE_CHECKS:
            with self.subTest(key=key):
                self.assertFalse(normal.validate({**result,key:False}))

    def test_false_claims_are_not_accepted(self):
        result=normal.capture(physical=False)
        for key in normal.FALSE_AUTHORITY:
            with self.subTest(key=key):
                self.assertFalse(normal.validate({**result,key:True}))

    def test_provider_and_task_counts_have_strict_types(self):
        result=normal.capture(physical=False)
        for key in ("provider_requests_forwarded","real_agent_tasks_executed"):
            for value in (True,1,-1):
                with self.subTest(key=key,value=value):
                    self.assertFalse(normal.validate({**result,key:value}))

    def test_stale_or_wrong_image_pin_denied(self):
        result=normal.capture(physical=False)
        for bad in ("latest","sha256:"+("Z"*64),"sha256:0","sha256:xyz",123):
            with self.subTest(value=bad):
                self.assertFalse(normal.validate({
                    **result,"exact_running_base_image":bad}))

    def test_physical_result_requires_exact_overlay_object(self):
        result=normal.capture(physical=False)
        result.update({k:True for k in normal.TRUE_CHECKS})
        result["exact_running_base_image"]=TEST_IMAGE
        result["physical_canary_executed"]=True
        self.assertFalse(normal.validate(result))
        result["disposable_reproducible_overlay_image"]=TEST_IMAGE
        self.assertTrue(normal.validate(result))

    def test_bad_or_unknown_schema_denied(self):
        result=normal.capture(physical=False)
        self.assertFalse(normal.validate({**result,"schema":True}))
        self.assertFalse(normal.validate({**result,"schema":2}))
        self.assertFalse(normal.validate({**result,"mode":"model_router"}))
        self.assertFalse(normal.validate({**result,"decision":"GO"}))

    def test_missing_or_extra_fields_denied(self):
        result=normal.capture(physical=False)
        self.assertFalse(normal.validate({**result,"provider_id":"dangerous"}))
        self.assertFalse(normal.validate({
            k:v for k,v in result.items() if k!="decision"}))

    def test_blockers_must_remain_on_green(self):
        result=normal.capture(physical=False)
        self.assertFalse(normal.validate({**result,"blockers":[]}))
        for blocker in normal.BLOCKERS:
            with self.subTest(blocker=blocker):
                self.assertFalse(normal.validate({
                    **result,"blockers":[x for x in normal.BLOCKERS if x!=blocker]}))

    def test_reproducibility_contract_uses_stable_exclusive_base_tag(self):
        self.assertEqual(
            normal.STABLE_BASE_TAG,
            "og-hermes-normal-base-"+"0"*16)
        self.assertEqual(normal.STABLE_BASE_LOCK.parent,
                         shell.SCRATCH_PARENT)
        with patch.object(normal,"_absent_docker_object",return_value=True):
            with normal._exclusive_fixed_base_tag():
                self.assertTrue(normal.STABLE_BASE_LOCK.is_dir())
                with self.assertRaises(FileExistsError):
                    with normal._exclusive_fixed_base_tag():
                        pass
        self.assertFalse(normal.STABLE_BASE_LOCK.exists())

    def test_preexisting_base_image_is_not_retagged(self):
        with patch.object(normal,"_absent_docker_object",return_value=False):
            with self.assertRaises(normal.UnsafeNormalStartup):
                with normal._exclusive_fixed_base_tag():
                    pass
        self.assertFalse(normal.STABLE_BASE_LOCK.exists())

    def test_exact_reproducible_docker_build_argv(self):
        with tempfile.TemporaryDirectory(
                prefix="og-hermes-normal-fixture-",dir=shell.SCRATCH_PARENT) as root:
            ctx=Path(root)/"docker-build"
            ctx.mkdir()
            base=normal._safe_tag("base",SUFFIX)
            overlay=normal._safe_tag("overlay-a",SUFFIX)
            cmd=normal._build_argv(ctx,base,overlay)
            self.assertIn("--pull=false",cmd)
            self.assertIn("--no-cache",cmd)
            self.assertIn("--network=none",cmd)
            self.assertIn("--provenance=false",cmd)
            self.assertEqual(cmd[cmd.index("--build-arg")+1],f"BASE={base}")
            self.assertIn(f"SOURCE_DATE_EPOCH={normal.SOURCE_DATE_EPOCH}",cmd)
            self.assertNotIn("docker.sock"," ".join(cmd))

    def test_invalid_docker_image_refs_denied(self):
        for kind,suffix in (("production","a"*16),("base","/bad"),
                            ("overlay-a","A"*16),("base","a"*17)):
            with self.subTest(kind=kind,suffix=suffix):
                with self.assertRaises(normal.UnsafeNormalStartup):
                    normal._safe_tag(kind,suffix)

    def test_real_image_identity_must_be_sha256_object(self):
        with tempfile.TemporaryDirectory(
                prefix="og-hermes-normal-fixture-",dir=shell.SCRATCH_PARENT) as root:
            knowledge=Path(root)/"knowledge"
            workspace=Path(root)/"workspace"
            knowledge.mkdir();workspace.mkdir()
            name=f"og-hermes-normal-test-{SUFFIX}"
            for bad in ("latest","sha256:"+("Q"*64),"sha256:123"):
                with self.assertRaises(normal.UnsafeNormalStartup):
                    normal._container_argv(bad,name,knowledge,workspace)

    def test_container_has_real_normal_entrypoint_no_override(self):
        with tempfile.TemporaryDirectory(
                prefix="og-hermes-normal-fixture-",dir=shell.SCRATCH_PARENT) as root:
            knowledge=Path(root)/"knowledge"
            workspace=Path(root)/"workspace"
            knowledge.mkdir();workspace.mkdir()
            command=normal._container_argv(
                TEST_IMAGE,f"og-hermes-normal-test-{SUFFIX}",
                knowledge,workspace)
            self.assertEqual(command[0:2],["docker","create"])
            self.assertNotIn("--entrypoint",command)
            self.assertNotIn("--entrypoint=/bin/sh",command)
            self.assertEqual(
                command[-4:],
                ["python3","-B","-m","assistx.strict_executor_main"])
            self.assertIn("--network=none",command)
            self.assertIn("--read-only",command)
            self.assertIn("--user=1000:1000",command)
            self.assertIn("--cap-drop=ALL",command)
            self.assertIn("--security-opt=no-new-privileges:true",command)
            self.assertIn("--pull=never",command)
            self.assertIn("--pids-limit=32",command)
            self.assertIn("--memory=384m",command)
            self.assertNotIn("id_hermes"," ".join(command))
            self.assertNotIn("SSD_4TB"," ".join(command))
            self.assertNotIn("NAS5"," ".join(command))

    def test_disposable_synthetic_only_two_mounts(self):
        with tempfile.TemporaryDirectory(
                prefix="og-hermes-normal-fixture-",dir=shell.SCRATCH_PARENT) as root:
            knowledge=Path(root)/"knowledge"
            workspace=Path(root)/"workspace"
            knowledge.mkdir();workspace.mkdir()
            command=normal._container_argv(
                TEST_IMAGE,f"og-hermes-normal-test-{SUFFIX}",knowledge,workspace)
            mounts=[
                command[i+1] for i,arg in enumerate(command[:-1])
                if arg=="--mount"
            ]
            self.assertEqual(len(mounts),2)
            self.assertIn(",readonly",mounts[0])
            self.assertNotIn(",readonly",mounts[1])
            self.assertIn("dst=/research/knowledge",mounts[0])
            self.assertIn("dst=/workspace",mounts[1])

    def test_outside_private_scratch_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            knowledge=Path(root)/"knowledge";knowledge.mkdir()
            worktree=Path(root)/"worktree";worktree.mkdir()
            with self.assertRaises(normal.UnsafeNormalStartup):
                normal._container_argv(
                    TEST_IMAGE,f"og-hermes-normal-test-{SUFFIX}",
                    knowledge,worktree)

    def test_refuses_production_checkout_as_mount(self):
        real=Path("/home/scott/git/agent-harness")
        with tempfile.TemporaryDirectory(
                prefix="og-hermes-normal-fixture-",dir=shell.SCRATCH_PARENT) as root:
            ro=Path(root)/"knowledge"
            ro.mkdir()
            with self.assertRaises(normal.UnsafeNormalStartup):
                normal._container_argv(
                    TEST_IMAGE,f"og-hermes-normal-test-{SUFFIX}",ro,real)

    def test_symlink_to_real_host_path_denied(self):
        with tempfile.TemporaryDirectory(
                prefix="og-hermes-normal-fixture-",dir=shell.SCRATCH_PARENT) as root:
            knowledge=Path(root)/"knowledge"
            knowledge.symlink_to(Path("/home/scott/git/agent-harness"))
            workspace=Path(root)/"workspace";workspace.mkdir()
            with self.assertRaises(normal.UnsafeNormalStartup):
                normal._container_argv(
                    TEST_IMAGE,f"og-hermes-normal-test-{SUFFIX}",
                    knowledge,workspace)

    def test_fixture_contract_contains_patched_one_poll_only(self):
        content=normal.SITE.read_text()
        self.assertIn("_real_run_loop(once=True)",content)
        self.assertIn("patch(\"requests.request\"",content)
        self.assertIn("patch.object(subprocess, \"Popen\"",content)
        self.assertIn("ROOTLESS_HERMES_NORMAL_MAIN_PASS",content)
        self.assertIn("SYNTHETIC_BOOTSTRAP_ONLY",content)

    def test_rootless_entrypoint_refuses_root_and_credential_serialization(self):
        content=normal.ENTRY.read_text()
        self.assertIn('root execution denied',content)
        self.assertIn('refuses credential environment seeding',content)
        self.assertNotIn('env |',content)
        self.assertIn('exec "$@"',content)

    def test_builder_does_not_mutate_source_and_uses_fixed_context(self):
        with tempfile.TemporaryDirectory(
                prefix="og-hermes-normal-fixture-",dir=shell.SCRATCH_PARENT) as root:
            before=normal.sha256_file(normal.ENTRY)
            ctx=normal._dockerfile_context(Path(root))
            self.assertEqual(normal.sha256_file(ctx/"entrypoint.sh"),before)
            self.assertEqual(normal.sha256_file(normal.ENTRY),before)
            self.assertIn("FROM ${BASE}",(ctx/"Dockerfile").read_text())
            self.assertEqual(
                int((ctx/"entrypoint.sh").stat().st_mtime),
                int(normal.SOURCE_DATE_EPOCH))
            self.assertEqual(
                int((ctx/"Dockerfile").stat().st_mtime),
                int(normal.SOURCE_DATE_EPOCH))

    def test_cannot_claim_physical_completion_without_live_pin(self):
        record=normal.capture(physical=False)
        record.update({k:True for k in normal.TRUE_CHECKS})
        record["physical_canary_executed"]=True
        self.assertFalse(normal.validate(record))


if __name__=="__main__":
    unittest.main()
