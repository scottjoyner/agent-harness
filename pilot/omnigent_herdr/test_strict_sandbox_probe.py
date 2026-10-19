import subprocess
import unittest
from pathlib import Path

from strict_sandbox_probe import REQUIRED, inspect


def pretend(argv, *, evidence=REQUIRED, returncode=0, **_):
    i = argv.index('--bind')
    Path(argv[i + 1], 'proof').write_text('sandboxed', encoding='utf-8')
    return subprocess.CompletedProcess(argv, returncode, '\n'.join(evidence), '')


class StrictSandboxTests(unittest.TestCase):
    def test_missing_bwrap_is_denied(self):
        r = inspect(which=lambda _: None)
        self.assertFalse(r['strict_isolation_passed'])
        self.assertFalse(r['agent_dispatch_authorized'])

    def test_complete_witness_does_not_authorize_dispatch(self):
        r = inspect(which=lambda _: '/usr/bin/bwrap', runner=pretend)
        self.assertTrue(r['strict_isolation_passed'])
        self.assertFalse(r['agent_dispatch_authorized'])
        self.assertFalse(r['network_available_to_agent'])

    def test_missing_secret_read_denial_is_rejected(self):
        r = inspect(which=lambda _: '/usr/bin/bwrap',
                    runner=lambda a, **kw: pretend(
                        a, evidence=REQUIRED - {'OUTSIDE_READ_DENIED'}, **kw))
        self.assertFalse(r['strict_isolation_passed'])

    def test_missing_pid_isolation_is_rejected(self):
        r = inspect(which=lambda _: '/usr/bin/bwrap',
                    runner=lambda a, **kw: pretend(
                        a, evidence=REQUIRED - {'PID_ISOLATED'}, **kw))
        self.assertFalse(r['strict_isolation_passed'])

    def test_missing_no_new_privileges_is_rejected(self):
        r = inspect(which=lambda _: '/usr/bin/bwrap',
                    runner=lambda a, **kw: pretend(
                        a, evidence=REQUIRED - {'NO_NEW_PRIVILEGES'}, **kw))
        self.assertFalse(r['strict_isolation_passed'])

    def test_nonzero_process_exit_is_rejected(self):
        r = inspect(which=lambda _: '/usr/bin/bwrap',
                    runner=lambda a, **kw: pretend(a, returncode=1, **kw))
        self.assertFalse(r['strict_isolation_passed'])

    def test_network_and_home_are_unavailable_in_arguments(self):
        def argv_witness(argv, **kw):
            self.assertIn('--unshare-net', argv)
            self.assertIn('--unshare-pid', argv)
            self.assertIn('--clearenv', argv)
            self.assertIn('--cap-drop', argv)
            ro_bind_targets = [
                argv[i + 2] for i, a in enumerate(argv[:-2]) if a == '--ro-bind'
            ]
            bind_targets = [
                argv[i + 2] for i, a in enumerate(argv[:-2]) if a == '--bind'
            ]
            self.assertEqual(ro_bind_targets, ['/usr'])
            self.assertEqual(bind_targets, ['/workspace'])
            return pretend(argv, **kw)
        self.assertTrue(inspect(which=lambda _: '/usr/bin/bwrap',
                                runner=argv_witness)['strict_isolation_passed'])


if __name__ == '__main__':
    unittest.main()
