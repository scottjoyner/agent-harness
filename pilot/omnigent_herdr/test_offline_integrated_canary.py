import unittest
from pathlib import Path

from offline_integrated_canary import inspect, sandbox_command, exit_status


class OfflineIntegratedCanaryTests(unittest.TestCase):
    def test_failed_cli_exit_code(self):
        self.assertEqual(exit_status({}), 2)
        self.assertEqual(exit_status({"integrated_synthetic_passed": True}), 0)

    def test_missing_sandbox_blocks_before_any_git_action(self):
        result = inspect(which=lambda n: None if n == 'bwrap' else '/usr/bin/git')
        self.assertFalse(result['integrated_synthetic_passed'])
        self.assertFalse(result['agent_dispatch_authorized'])
        self.assertEqual(result['provider_requests_forwarded'], 0)
        self.assertFalse(result['used_real_repository'])

    def test_missing_git_blocks_before_any_runtime_action(self):
        result = inspect(which=lambda n: None if n == 'git' else '/usr/bin/bwrap')
        self.assertFalse(result['integrated_synthetic_passed'])
        self.assertFalse(result['remote_push_attempted'])

    def test_sandbox_has_no_host_root_or_home_mount(self):
        cmd = sandbox_command(
            '/usr/bin/bwrap', Path('/tmp/fixture/clone'),
            Path('/tmp/fixture/transport'), Path('/tmp/fixture/secret'),
            Path('/tmp/fixture/sentinel'), 'synthetic')
        self.assertIn('--unshare-net', cmd)
        self.assertIn('--unshare-pid', cmd)
        self.assertIn('--unshare-user', cmd)
        self.assertIn('--clearenv', cmd)
        self.assertIn('--cap-drop', cmd)
        self.assertIn('--new-session', cmd)
        mounts = [(cmd[i], cmd[i+1], cmd[i+2])
                  for i in range(len(cmd)-2)
                  if cmd[i] in ('--bind', '--ro-bind')]
        self.assertEqual(
            mounts,
            [('--ro-bind', '/usr', '/usr'),
             ('--bind', '/tmp/fixture/clone', '/workspace'),
             ('--ro-bind', '/tmp/fixture/transport', '/run/fleet-broker')])
        self.assertNotIn(('/', '/'), [(src, dst) for _, src, dst in mounts])
        self.assertNotIn('/home', [dst for _, _, dst in mounts])

    def test_sandbox_executes_only_python_fixture(self):
        cmd = sandbox_command(
            'bwrap', Path('/tmp/work'), Path('/tmp/socket'),
            Path('/tmp/private'), Path('/tmp/guard'), 'n')
        split = cmd.index('--')
        self.assertEqual(cmd[split+1:split+4],
                         ['/usr/bin/python3', '-c', cmd[split+3]])
        self.assertNotIn('opencode', ' '.join(cmd[:split]))
        self.assertNotIn('claude', ' '.join(cmd[:split]))
        self.assertNotIn('freebuff', ' '.join(cmd[:split]))


if __name__ == '__main__':
    unittest.main()
