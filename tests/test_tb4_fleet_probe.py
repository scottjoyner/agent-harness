import unittest
from tb4.fleet_probe import score, quantile, CASES, perform


class FleetProbeTests(unittest.TestCase):
    def test_strict_exact_matching(self):
        self.assertTrue(score('  Paris\n','Paris'))
        self.assertFalse(score('Paris is the capital of France','Paris'))
        self.assertFalse(score('', 'Paris'))

    def test_percentile_and_public_case_count(self):
        self.assertEqual(len(CASES),8)
        self.assertEqual(quantile([1,2,3,4,5],.95),4.8)
        self.assertIsNone(quantile([],.5))

    def test_reject_nonprivate_endpoint(self):
        with self.assertRaises(ValueError):
            perform('http://example.com/v1', 'model')
        with self.assertRaises(ValueError):
            perform('http://127.0.0.1:1234/not-v1','model')

if __name__ == '__main__':
    unittest.main()
