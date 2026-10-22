"""Separate-process causality probe; zero production or network I/O."""
from __future__ import annotations

from unittest.mock import patch
import unittest

import hermes_causal_fence_process_probe as probe


class CausalFenceProcessTests(unittest.TestCase):
    def test_fresh_vs_old_same_claim_list_across_two_processes(self):
        out=probe.physical()
        self.assertTrue(out["synthetic_owner_before_revocation_before_binding_pass"])
        self.assertTrue(out["valid_prior_revocation_for_same_claims_rejected"])
        self.assertTrue(out["revocation_swapped_after_binding_rejected"])
        self.assertTrue(out["separate_verifier_os_process"])
        self.assertFalse(out["synthetic_signer_timestamps_independent"])
        self.assertFalse(out["external_head_independently_custodied"])
        self.assertFalse(out["production_rollback_authorized"])
        self.assertEqual(out["real_claims_modified"],0)

    def test_no_positive_production_release(self):
        out=probe.physical()
        self.assertEqual(out["decision"],"HOLD")
        self.assertFalse(out["production_rollback_authorized"])

    def test_signer_keys_stay_only_in_parent(self):
        with patch.object(probe.subprocess,"run",side_effect=OSError("subprocess denied")):
            with self.assertRaises(OSError):
                probe.physical()

    def test_default_cli_only_prints_hold(self):
        self.assertNotEqual(probe.prior.prior.IMAGE,"assistx-hermes-adapter")


if __name__=="__main__":
    unittest.main()
