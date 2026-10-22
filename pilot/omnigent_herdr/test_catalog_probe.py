import json
import os
from unittest.mock import patch
import subprocess
import unittest

from catalog_probe import first_tokens, probe
from pathlib import Path

ROSTER = json.loads(Path(__file__).with_name("roster.json").read_text())


def fake_output(command, **_):
    cli = command[0].rsplit("/", 1)[-1]
    values = {
        "kilo": "kilo/kilo-auto/free\n",
        "opencode": "opencode/deepseek-v4.1-flash-free\n",
        "cmd": "deepseek/deepseek-v4.1-flash    short description\n",
        "blink": "deepseek/deepseek-v4-flash\n",
    }
    return subprocess.CompletedProcess(command, 0, values[cli], "")


class CatalogTests(unittest.TestCase):
    def test_first_column_exact_only(self):
        self.assertEqual(first_tokens("alias deepseek/deepseek-v4-flash\n"), {"alias"})

    def test_listed_catalogs_not_dispatch_authority(self):
        r = probe(ROSTER, which=lambda n: "/bin/" + n, runner=fake_output)
        self.assertFalse(r["requested_catalogs_listed"])
        self.assertTrue(all(r["catalogs"][x]["catalog_listed"] for x in
                            ("kilo", "opencode", "cmdcode")))
        self.assertEqual(r["catalogs"]["freebuff"]["status"], "requires_sandbox")
        self.assertFalse(r["model_pins_verified"])
        self.assertFalse(r["authentication_verified"])
        self.assertFalse(r["agent_dispatch_authorized"])

    def test_provider_secrets_never_inherited(self):
        captured = []
        def witness(command, **kw):
            captured.append(kw["env"])
            return fake_output(command, **kw)
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "do-not-pass",
            "ANTHROPIC_API_KEY": "do-not-pass",
            "MY_UNKNOWN_SECRET": "do-not-pass",
        }):
            probe(ROSTER, which=lambda n: "/bin/" + n, runner=witness)
        self.assertEqual(len(captured), 3)
        for env in captured:
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("MY_UNKNOWN_SECRET", env)

    def test_blink_not_invoked_as_false_catalog(self):
        seen = []
        def witness(command, **kw):
            seen.append(command[0])
            return fake_output(command, **kw)
        r = probe(ROSTER, which=lambda n: "/bin/" + n, runner=witness)
        self.assertEqual(r["catalogs"]["freebuff"]["status"], "requires_sandbox")
        self.assertEqual(len(seen), 3)
        self.assertFalse(any(x.endswith("/blink") for x in seen))

    def test_missing_native_freebuff_not_qualified(self):
        seen = []
        def witness(command, **kw):
            seen.append(command[0])
            return fake_output(command, **kw)
        r = probe(ROSTER, which=lambda n: None if n == "freebuff" else "/bin/" + n,
                  runner=witness)
        self.assertFalse(r["requested_catalogs_listed"])
        self.assertEqual(r["catalogs"]["freebuff"]["status"], "unverified")
        self.assertFalse(any(x.endswith("/blink") for x in seen))

    def test_missing_catalog_denied(self):
        r = probe(ROSTER, which=lambda n: None if n == "blink" else "/bin/" + n,
                  runner=fake_output)
        self.assertFalse(r["requested_catalogs_listed"])
        self.assertEqual(r["catalogs"]["freebuff"]["status"], "unverified")

    def test_nonzero_exit_denied_even_if_model_printed(self):
        def failed(command, **kwargs):
            output = fake_output(command, **kwargs)
            return subprocess.CompletedProcess(command, 1, output.stdout, "")
        r = probe(ROSTER, which=lambda n: "/bin/" + n, runner=failed)
        self.assertFalse(r["requested_catalogs_listed"])
        self.assertTrue(all(not x["catalog_listed"] for x in r["catalogs"].values()))

    def test_timeout_denied(self):
        def timeout(command, **kw):
            raise subprocess.TimeoutExpired(command, kw["timeout"])
        r = probe(ROSTER, which=lambda n: "/bin/" + n, runner=timeout)
        self.assertFalse(r["requested_catalogs_listed"])


if __name__ == "__main__":
    unittest.main()
