import unittest
from collections import namedtuple
from bootstrap_audit import COMMANDS, report

Disk = namedtuple('Disk', 'total used free')


class AuditTests(unittest.TestCase):
    def test_complete_inventory_does_not_authorize_dispatch(self):
        data = report(which=lambda name: '/usr/bin/' + name,
                      hostname=lambda: 'scott-Lenovo-ideapad-330S-15IKB',
                      disk=lambda _: Disk(100, 20, 80))
        self.assertTrue(data['all_prerequisites_present'])
        self.assertFalse(data['agent_dispatch_authorized'])
        self.assertFalse(data['installation_authorized'])

    def test_missing_binary_blocks_readiness(self):
        data = report(which=lambda name: None if name == 'cmd-acp' else '/usr/bin/' + name,
                      hostname=lambda: 'lenovo', disk=lambda _: Disk(100, 20, 80))
        self.assertFalse(data['all_prerequisites_present'])
        self.assertIsNone(data['executables']['cmd-acp'])

    def test_registry_includes_all_requested_agents(self):
        self.assertTrue({'claude', 'codex', 'kilo', 'opencode', 'blink', 'cmd-acp'}.issubset(COMMANDS))
