"""Offline tests for the official TB4 runner's safety and reproducibility."""
import argparse
from pathlib import Path
import unittest
from unittest.mock import patch
from tb4.runner import DATASET, make_command, normalize_endpoint, check_model


def example(**kw):
    config = dict(harbor=Path("/tmp/harbor"), model="openai/test-model",
                  endpoint="http://xwing:1234/v1", key_env="TB4_LOCAL_API_KEY",
                  job_name="test-20261028", tasks=1, attempts=1,
                  concurrency=1, max_turns=12, pi_version="0.85.1",
                  include=[], mode="preflight")
    config.update(kw)
    return argparse.Namespace(**config)


class RunnerTests(unittest.TestCase):
    def test_pins_official_dataset_and_pi(self):
        cmd = make_command(example())
        self.assertEqual(cmd[cmd.index("-d") + 1], DATASET)
        self.assertEqual(cmd[cmd.index("--agent") + 1], "pi")
        self.assertEqual(cmd[cmd.index("--n-concurrent") + 1], "1")
        bridged = make_command(example(), endpoint_override="http://172.17.0.1:22332/v1")
        self.assertEqual(bridged[bridged.index("--base-url") + 1], "http://172.17.0.1:22332/v1")
        self.assertIn("--dry-run", cmd)
        self.assertIn("version=0.85.1", cmd)

    def test_no_secret_is_placed_in_cli(self):
        cmd = " ".join(make_command(example()))
        self.assertIn("TB4_LOCAL_API_KEY", cmd)
        self.assertNotIn("local-no-auth", cmd)

    def test_run_does_not_use_dry_run(self):
        cmd = make_command(example(mode="run", tasks=3, include=["foo-*"]))
        self.assertNotIn("--dry-run", cmd)
        self.assertIn("--include-task-name", cmd)
        self.assertIn("terminal-bench/foo-*", cmd)
        self.assertEqual(cmd[cmd.index("--n-tasks") + 1], "3")

    def test_rejects_unsafe_url_and_inputs(self):
        for endpoint in ["http://example.com/v1", "http://user:pass@xwing/v1",
                         "http://xwing/v1?token=secret", "file:///tmp/thing"]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                normalize_endpoint(endpoint)
        with self.assertRaises(ValueError):
            make_command(example(concurrency=30))
        with self.assertRaises(ValueError):
            make_command(example(include=["x;touch /tmp/foo"]))
        self.assertEqual(normalize_endpoint("http://xwing:1234/v1/"), "http://xwing:1234/v1")
        self.assertEqual(normalize_endpoint("http://100.83.215.83:1234/v1"), "http://100.83.215.83:1234/v1")

    def test_model_name_must_be_in_catalog(self):
        import io, json
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()
        body = json.dumps({"data": [{"id": "test-model"}]}).encode()
        with patch("tb4.runner.urlopen", return_value=Response(body)):
            self.assertEqual(check_model("http://xwing:1234/v1", "openai/test-model", "NO_KEY"),
                             {"model_available": True, "catalog_size": 1})
        with patch("tb4.runner.urlopen", return_value=Response(body)):
            with self.assertRaises(RuntimeError):
                check_model("http://xwing:1234/v1", "openai/wrong", "NO_KEY")


if __name__ == "__main__":
    unittest.main()
