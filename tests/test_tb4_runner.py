"""Offline tests for the official TB4 runner's safety and reproducibility."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from tb4.runner import (DATASET, make_command, normalize_endpoint, check_model,
                        trace_output_preflight, run_with_trace)


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

    def test_trace_preflight_checks_owner_only_path_space_and_journal_cap(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            private=base/'private'
            private.mkdir(mode=0o700)
            journal=private/'events.jsonl'
            with self.assertRaisesRegex(ValueError,'free-space threshold'):
                trace_output_preflight(journal,0)
            with patch('tb4.runner.shutil.disk_usage',return_value=SimpleNamespace(free=30*1024**3)):
                info=trace_output_preflight(journal,20)
                self.assertEqual(info['journal_remaining_bytes'],32*1024**2)
                self.assertEqual(info['telemetry_volume_free_gib'],30)
                journal.write_bytes(b'contents')
                journal.chmod(0o600)
                self.assertLess(trace_output_preflight(journal,20)['journal_remaining_bytes'],32*1024**2)
                link=private/'journal-link'
                link.symlink_to(journal)
                with self.assertRaisesRegex(ValueError,'nonsymlink'):
                    trace_output_preflight(link,20)
                journal.chmod(0o644)
                with self.assertRaisesRegex(ValueError,'0600'):
                    trace_output_preflight(journal,20)
                journal.chmod(0o600)
                with patch('tb4.runner.shutil.disk_usage',return_value=SimpleNamespace(free=7*1024**3)):
                    with self.assertRaisesRegex(RuntimeError,'insufficient telemetry filesystem'):
                        trace_output_preflight(journal,20)
                with patch('tb4.runner.shutil.disk_usage',return_value=SimpleNamespace(free=30*1024**3)):
                    # Sparse file occupies no physical 32MiB but still must
                    # block a new long benchmark at the logical journal cap.
                    with journal.open('wb') as stream:stream.truncate(32*1024**2-512*1024)
                    with self.assertRaisesRegex(RuntimeError,'approaching its hard capacity'):
                        trace_output_preflight(journal,20)
                private.chmod(0o755)
                with self.assertRaisesRegex(ValueError,'0700'):
                    trace_output_preflight(journal,20)

    def test_trace_supervisor_success_emits_stop_after_active(self):
        process=Mock()
        process.__enter__=Mock(return_value=process)
        process.__exit__=Mock(return_value=None)
        process.poll.side_effect=[None,0]
        process.wait.return_value=0
        reader=Mock()
        with patch('tb4.runner.subprocess.Popen',return_value=process),patch('tb4.runner.time.sleep'):
            self.assertEqual(run_with_trace(['harbor'],None,{},reader,Path('/tmp/test.jsonl')),0)
        self.assertEqual([c.kwargs['publisher_state'] for c in reader.export_to.call_args_list],['active','stopped'])
        process.terminate.assert_not_called()

    def test_trace_supervisor_terminates_harbor_on_publisher_failure(self):
        process=Mock()
        process.__enter__=Mock(return_value=process)
        process.__exit__=Mock(return_value=None)
        process.poll.return_value=None
        process.wait.return_value=143
        reader=Mock()
        reader.export_to.side_effect=OSError('synthetic ENOSPC')
        with patch('tb4.runner.subprocess.Popen',return_value=process):
            with self.assertRaisesRegex(OSError,'ENOSPC'):
                run_with_trace(['harbor'],None,{},reader,Path('/tmp/test.jsonl'))
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)
        self.assertEqual(reader.export_to.call_count,1) # no dishonest stopped

    def test_real_child_process_is_reaped_if_metadata_append_fails(self):
        # Actual disposable OS child, not Docker or Harbor. No long-running
        # process must survive an exporter failure at startup.
        with tempfile.TemporaryDirectory() as d:
            trace=Path(d)/'journal.jsonl'
            reader=Mock()
            reader.export_to.side_effect=OSError('synthetic journal ENOSPC')
            with (Path(d)/'child.log').open('w') as out:
                with self.assertRaisesRegex(OSError,'journal ENOSPC'):
                    run_with_trace([sys.executable,'-c','import time;time.sleep(20)'],
                                   out,{},reader,trace)
            self.assertEqual(reader.export_to.call_count,1)
            self.assertFalse(trace.exists())

    def test_trace_supervisor_escalates_if_harbor_ignores_terminate(self):
        process=Mock()
        process.__enter__=Mock(return_value=process)
        process.__exit__=Mock(return_value=None)
        process.poll.return_value=None
        process.wait.side_effect=[subprocess.TimeoutExpired('harbor',10),-9]
        reader=Mock()
        reader.export_to.side_effect=ValueError('synthetic telemetry capacity exceeded')
        with patch('tb4.runner.subprocess.Popen',return_value=process):
            with self.assertRaisesRegex(ValueError,'telemetry capacity'):
                run_with_trace(['harbor'],None,{},reader,Path('/tmp/test.jsonl'))
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_args_list[0].kwargs,{'timeout':10})
        self.assertEqual(process.wait.call_args_list[1].kwargs,{'timeout':5})
        self.assertEqual(reader.export_to.call_count,1)

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
