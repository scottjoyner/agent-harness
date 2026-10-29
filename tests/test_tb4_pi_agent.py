import unittest
from tb4.pi_agent import GuardedPi
from tb4.runner import GUARDED_AGENT, make_command
from test_tb4_runner import example


class PiAgentTests(unittest.TestCase):
    def test_guard_patches_extension_without_losing_turn_budget(self):
        source = GuardedPi._build_max_turns_extension(7)
        self.assertIn('const maxTurns = 7;', source)
        self.assertIn('pi.on("tool_call"', source)
        self.assertIn('pi.on("tool_result"', source)
        self.assertIn('return { block: true, reason:', source)
        self.assertIn('ctx.abort();', source)
        self.assertTrue(source.endswith('}\n'))

    def test_runner_opt_in_uses_custom_agent(self):
        command = make_command(example(guarded_pi=True))
        self.assertEqual(command[command.index('--agent') + 1], GUARDED_AGENT)
        normal = make_command(example())
        self.assertEqual(normal[normal.index('--agent') + 1], 'pi')


if __name__ == '__main__':
    unittest.main()
