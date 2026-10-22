import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from apparmor_vendor_probe import inspect


class AppArmorCandidateTests(unittest.TestCase):
    def test_vendor_candidate_not_admin_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "vendor"
            p.write_bytes(b"vendor profile test")
            d = hashlib.sha256(p.read_bytes()).hexdigest()
            result = inspect(p, expected_digest=d, restriction_value="1",
                             which=lambda _: "/usr/sbin/apparmor_parser",
                             runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
            self.assertTrue(result["candidate_qualified"])
            self.assertFalse(result["profile_loaded"])
            self.assertFalse(result["administrator_action_authorized"])
            self.assertFalse(result["agent_dispatch_authorized"])

    def test_swapped_profile_denied_before_parser(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "profile"
            p.write_text("unauthorized", encoding="utf-8")
            def must_not_run(*a, **k):
                self.fail("Parser should not run for untrusted profile")
            r = inspect(p, expected_digest="0" * 64, restriction_value="1",
                        runner=must_not_run)
            self.assertFalse(r["candidate_qualified"])
            self.assertFalse(r["profile_digest_matches"])

    def test_disabled_global_restriction_denied(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "profile"
            p.write_bytes(b"vendor")
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            r = inspect(p, expected_digest=digest, restriction_value="0",
                        which=lambda _: "/usr/sbin/apparmor_parser",
                        runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
            self.assertFalse(r["candidate_qualified"])

    def test_parser_failure_denied(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "profile"
            p.write_bytes(b"vendor")
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            r = inspect(p, expected_digest=digest, restriction_value="1",
                        which=lambda _: "/usr/sbin/apparmor_parser",
                        runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "invalid"))
            self.assertFalse(r["candidate_qualified"])
            self.assertFalse(r["parser_valid"])

    def test_symlink_denied(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "source"
            p.write_bytes(b"vendor")
            link = Path(td) / "link"
            link.symlink_to(p)
            r = inspect(link, restriction_value="1")
            self.assertFalse(r["candidate_qualified"])


if __name__ == "__main__":
    unittest.main()
