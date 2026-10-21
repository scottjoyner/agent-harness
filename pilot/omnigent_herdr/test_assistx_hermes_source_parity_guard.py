"""Negative contract tests for real Hermes image/live-source parity canary."""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import assistx_hermes_source_parity_guard as parity


class HermesParityContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.src = Path(self.temp.name) / "deployment"
        self.src.mkdir()
        self.app = self.src / "src"
        self.app.mkdir()
        self.image = "sha256:" + "a"*64
        self.commit = "b"*40

    def test_pinned_image_deny_network_and_host_mounts(self):
        argv = parity.image_hash_command(self.image)
        for flag in ("--rm", "--pull=never", "--network=none",
                     "--read-only", "--user=1000:1000",
                     "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                     "--entrypoint=/usr/bin/sha256sum"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-4:], list(parity.FILES))
        self.assertNotIn("--mount", argv)
        self.assertNotIn("--volume", argv)
        self.assertNotIn("--env", argv)

    def test_rejects_mutable_or_malformed_image(self):
        for image in ("latest", "assistx-hermes:latest", "sha256:"+"z"*64,
                      "sha256:"+"a"*63, "", True):
            with self.subTest(image=image), self.assertRaises(
                    (ValueError, TypeError)):
                parity.image_hash_command(image)

    def test_exact_four_lines_only(self):
        pairs = {p: str(i)*64 for i,p in enumerate(parity.FILES, start=1)}
        data = "".join(f"{d}  {p}\n" for p,d in pairs.items())
        self.assertEqual(parity.parse_image_hashes(data), pairs)

    def test_duplicate_and_malformed_hash_lines_denied(self):
        paths = list(parity.FILES)
        valid = [f"{i}"*64+"  "+p for i,p in enumerate(paths, 1)]
        mutants = [
            "\n".join(valid[:-1]),
            "\n".join(valid+[valid[-1]]),
            "\n".join([valid[0],valid[0],valid[2],valid[3]]),
            "\n".join(["X"*64+"  "+paths[0],*valid[1:]]),
            "\n".join([valid[0]+" ../../escape",*valid[1:]]),
            "\n".join(["A"*4000,*valid]),
        ]
        for sample in mutants:
            with self.subTest(sample=sample[:30]), self.assertRaises(ValueError):
                parity.parse_image_hashes(sample)

    def test_rw_source_bind_observed_not_approved(self):
        mount = {"Type": "bind", "Source": str(self.app),
                 "Destination": "/app/src", "RW": True}
        with patch.object(parity, "SOURCE", self.src):
            self.assertTrue(parity.inspect_mounts([mount]))
            self.assertFalse(parity.inspect_mounts([dict(mount, RW=False)]))
            self.assertFalse(parity.inspect_mounts([mount, mount]))
            self.assertFalse(parity.inspect_mounts([dict(mount, Type="volume")]))
            self.assertFalse(parity.inspect_mounts([dict(mount, Source="/etc")]))

    def test_source_symlink_escape_denied(self):
        target = Path(self.temp.name) / "elsewhere"
        target.mkdir()
        (self.src / "src_alias").symlink_to(target, target_is_directory=True)
        with patch.object(parity, "SOURCE", self.src):
            self.assertFalse(parity.inspect_mounts([{
                "Type": "bind", "Source": str(target),
                "Destination": "/app/src", "RW": True,
            }]))

    def test_dry_run_never_accesses_docker_or_git(self):
        with patch.object(parity, "command") as run:
            record = parity.capture()
        run.assert_not_called()
        self.assertTrue(parity.verify(record))
        self.assertEqual(record["decision"], "HOLD")
        self.assertFalse(record["physical_readonly_check_executed"])

    def test_wrong_host_never_inspects_docker(self):
        with patch.object(parity.os, "uname") as uname, \
             patch.object(parity, "command") as cmd:
            uname.return_value.nodename = "beelink"
            report = parity.capture(physical=True)
        cmd.assert_not_called()
        self.assertIn("physical_parity_check_failed_closed", report["blockers"])

    def test_partial_success_never_valid(self):
        report = parity.capture()
        for key, value in (
            ("four_critical_file_hashes_match", True),
            ("files_checked", 4),
            ("live_source_bind_verified_rw", True),
            ("physical_readonly_check_executed", True),
        ):
            with self.subTest(key=key):
                forged = dict(report)
                forged[key] = value
                self.assertFalse(parity.verify(forged))

    def test_all_green_parity_stays_non_authorizing(self):
        result = parity.capture()
        result.update({
            "physical_readonly_check_executed": True,
            "files_checked": 4,
            "four_critical_file_hashes_match": True,
            "live_source_bind_verified_rw": True,
            "local_checkout_commit": self.commit,
            "image_object_id": self.image,
        })
        self.assertTrue(parity.verify(result))
        self.assertFalse(result["source_commit_independently_attested"])
        self.assertFalse(result["production_source_mount_readonly"])
        self.assertFalse(result["production_deployment_authorized"])
        self.assertEqual(result["decision"], "HOLD")

    def test_forged_approval_or_provider_call_denied(self):
        for key, val in (
            ("source_commit_independently_attested", True),
            ("production_source_mount_readonly", True),
            ("source_owner_execution_authorized", True),
            ("production_deployment_authorized", True),
            ("production_containers_modified", True),
            ("provider_requests_forwarded", 1),
            ("decision", "GO"),
        ):
            with self.subTest(key=key):
                report = parity.capture()
                report[key] = val
                self.assertFalse(parity.verify(report))

    def test_invalid_metadata_and_missing_blockers(self):
        record = parity.capture()
        for key, val in (
            ("schema", True), ("files_checked", True),
            ("image_object_id", "sha256:bad"),
            ("local_checkout_commit", "main"),
            ("blockers", []),
        ):
            with self.subTest(key=key):
                modified = copy.deepcopy(record)
                modified[key] = val
                self.assertFalse(parity.verify(modified))


if __name__ == "__main__":
    unittest.main()
