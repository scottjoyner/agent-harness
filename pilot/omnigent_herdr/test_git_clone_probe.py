import unittest
from git_clone_probe import inspect


class GitCloneProbeTests(unittest.TestCase):
    def test_without_bwrap_fail_closed(self):
        result = inspect(which=lambda n: None if n == 'bwrap' else '/usr/bin/git')
        self.assertFalse(result['isolated_commit_verified'])
        self.assertFalse(result['agent_dispatch_authorized'])

    def test_without_git_fail_closed(self):
        result = inspect(which=lambda n: None if n == 'git' else '/usr/bin/bwrap')
        self.assertFalse(result['isolated_commit_verified'])
        self.assertFalse(result['remote_push_attempted'])
        self.assertFalse(result['used_real_repository'])


if __name__ == '__main__':
    unittest.main()
