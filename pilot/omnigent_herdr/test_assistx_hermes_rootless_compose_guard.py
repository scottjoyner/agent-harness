"""Deny-by-default validation of isolated rootless Hermes candidate Compose."""
from __future__ import annotations

import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import assistx_hermes_rootless_compose_guard as guard


class RootlessComposeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "og-hermes-containment-Aa_09Zx1"
        self.root.mkdir()
        self.ro = self.root / "knowledge"
        self.rw = self.root / "worktree"
        self.ro.mkdir()
        self.rw.mkdir()
        self.image = "sha256:" + "a"*64
        self.service = {
            "image": self.image,
            "pull_policy": "never",
            "network_mode": "none",
            "read_only": True,
            "user": "1000:1000",
            "restart": "no",
            "entrypoint": ["/entrypoint.sh"],
            "command": ["python3", "-B", "-m", "assistx.strict_executor_main"],
            "profiles": ["synthetic-canary"],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "environment": dict(guard.EXPECTED_ENV),
            "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,size=16777216"],
            "volumes": [
                {"type": "bind", "source": str(self.ro),
                 "target": "/research/knowledge", "read_only": True},
                {"type": "bind", "source": str(self.rw),
                 "target": "/workspace", "read_only": False},
            ],
        }

    def valid(self, service=None):
        with patch.object(guard, "SCRATCH_PARENT", self.parent):
            return guard.verify_render(
                {"services": {guard.SERVICE: service or self.service}},
                self.image, self.ro, self.rw,
            )

    def test_positive_exact_compose(self):
        self.assertTrue(self.valid())

    def test_extra_services_and_network_denied(self):
        with patch.object(guard, "SCRATCH_PARENT", self.parent):
            doc = {"services": {
                guard.SERVICE: self.service, "api": {"image": "busybox"}}}
            self.assertFalse(guard.verify_render(
                doc, self.image, self.ro, self.rw))
        for key, val in (
            ("ports", ["8000:8000"]),
            ("networks", ["host"]),
            ("extra_hosts", ["host.docker.internal:host-gateway"]),
            ("secrets", ["token"]), ("env_file", [".env"]),
            ("devices", ["/dev/sda"]), ("depends_on", ["api"]),
            ("build", "."), ("volumes_from", ["assistx-hermes-adapter"]),
        ):
            with self.subTest(key=key):
                svc = copy.deepcopy(self.service)
                svc[key] = val
                self.assertFalse(self.valid(svc))

    def test_broad_mount_and_rw_knowledge_denied(self):
        for source in ("/media/scott/SSD_4TB", "/media/scott/NAS5",
                       "/home/scott/knowledge", "/home/scott/.ssh"):
            with self.subTest(source=source):
                svc = copy.deepcopy(self.service)
                svc["volumes"][0]["source"] = source
                self.assertFalse(self.valid(svc))
        svc = copy.deepcopy(self.service)
        svc["volumes"][0]["read_only"] = False
        self.assertFalse(self.valid(svc))
        svc = copy.deepcopy(self.service)
        svc["volumes"].append({
            "type": "bind", "source": str(self.root),
            "target": "/app/src", "read_only": False})
        self.assertFalse(self.valid(svc))

    def test_symlink_fixture_denied(self):
        link = self.root / "linked"
        link.symlink_to(self.ro, target_is_directory=True)
        svc = copy.deepcopy(self.service)
        svc["volumes"][0]["source"] = str(link)
        with patch.object(guard, "SCRATCH_PARENT", self.parent):
            self.assertFalse(guard.verify_render(
                {"services": {guard.SERVICE: svc}},
                self.image, link, self.rw))

    def test_privileges_and_network_denied(self):
        for key, value in (
            ("network_mode", "host"), ("read_only", False),
            ("user", "0:1000"), ("restart", "always"),
            ("pull_policy", "always"),
            ("cap_drop", []), ("security_opt", []),
            ("entrypoint", ["/bin/sh"]),
            ("command", ["sh", "-c", "echo bypass"]),
            ("profiles", []),
        ):
            with self.subTest(key=key):
                svc = copy.deepcopy(self.service)
                svc[key] = value
                self.assertFalse(self.valid(svc))

    def test_unreviewed_image_denied(self):
        with patch.object(guard, "SCRATCH_PARENT", self.parent):
            for image in ("latest", "sha256:" + "z"*64, "hermes:latest", ""):
                with self.subTest(image=image):
                    self.assertFalse(guard.verify_render(
                        {"services": {guard.SERVICE: self.service}},
                        image, self.ro, self.rw))

    def test_no_credential_or_provider_extensions(self):
        svc = copy.deepcopy(self.service)
        svc["environment"]["OPENAI_API_KEY"] = "fake"
        self.assertFalse(self.valid(svc))
        svc = copy.deepcopy(self.service)
        svc["environment"]["ASSISTX_URL"] = "http://host.docker.internal:8000"
        self.assertFalse(self.valid(svc))
        svc = copy.deepcopy(self.service)
        svc["environment"]["HERMES_SELFTASK_ENABLED"] = "true"
        self.assertFalse(self.valid(svc))

    def test_entrypoint_script_rootless_without_env_seed(self):
        sh = Path(__file__).with_name("hermes_rootless_entrypoint_candidate.sh")
        text = sh.read_text()
        syntax = subprocess.run(["bash", "-n", str(sh)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(syntax.returncode, 0)
        self.assertIn("root execution denied", text)
        self.assertIn("credential environment seeding", text)
        self.assertNotIn("env | grep", text)
        self.assertNotIn("/root/.hermes/.env", text)

    def test_site_fixture_never_uses_real_network(self):
        p = Path(__file__).with_name("hermes_synthetic_sitecustomize.py")
        src = p.read_text()
        compile(src, str(p), "exec")
        self.assertIn('patch("requests.request", side_effect=synthetic_request)', src)
        self.assertIn('patch.object(subprocess, "Popen", side_effect=forbidden_process)', src)
        self.assertIn("once=True", src)
        self.assertNotIn("http://host.docker.internal", src)

    def test_config_template_has_no_inherited_compose(self):
        txt = guard.COMPOSE.read_text()
        self.assertIn("profiles: [synthetic-canary]", txt)
        self.assertIn("pull_policy: never", txt)
        self.assertIn("network_mode: none", txt)
        self.assertNotIn("env_file:", txt)
        self.assertNotIn("depends_on:", txt)


if __name__ == "__main__":
    unittest.main()
