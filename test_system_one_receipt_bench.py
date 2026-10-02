from __future__ import annotations

import unittest

import system_one_receipt_bench as bench


class SystemOneReceiptBenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = bench.load_json(bench.DEFAULT_MANIFEST)
        bench.validate_manifest(cls.manifest)

    def case(self, case_id: str):
        return bench.case_by_id(self.manifest, case_id)

    def receipt(self, case_id: str) -> dict:
        case = self.case(case_id)
        expected = case["expected"]["choices"]
        results = []
        for name, question in case["questions"].items():
            options = list(question["options"])
            choice = expected.get(name, options[0])
            selected = options.index(choice)
            remainder = 0.1 / (len(options) - 1)
            probabilities = [remainder for _ in options]
            probabilities[selected] = 0.9
            results.append(
                {
                    "name": name,
                    "type": question["type"],
                    "options": options,
                    "probabilities": probabilities,
                    "choice": choice,
                    "confidence": 0.9,
                    "noul": None,
                    "score": None,
                }
            )
        hashes = bench.hashes_for_case(case)
        return {
            "schema": "system-one-decision-receipt-v1",
            **hashes,
            "response_sha256": bench.sha256_json(results),
            "provider": {
                "provider_id": "fixture",
                "provider_version": "fixture-v1",
                "model_id": "fixture-model",
                "model_version": "1",
                "model_artifact_sha256": "a" * 64,
                "runtime": {"mode": "offline-test"},
            },
            "latency_ms": 1.5,
            "questions": results,
            "resolver_result": None,
            "evidence_only": True,
            "authority": dict(bench.AUTHORITY),
            "metadata": {},
        }

    def test_manifest_is_offline_non_destructive_and_untrusted_page_text(self) -> None:
        semantics = self.manifest["semantics"]
        self.assertTrue(semantics["non_destructive"])
        self.assertFalse(semantics["browser_execution"])
        self.assertFalse(semantics["network_access"])
        self.assertTrue(semantics["page_text_is_untrusted_data"])
        self.assertFalse(semantics["authority_changes_allowed"])

    def test_valid_receipt_binds_frozen_fixture(self) -> None:
        result = bench.validate_receipt(
            self.case("BD-001"),
            self.receipt("BD-001"),
        )
        self.assertTrue(result["passed"])
        self.assertTrue(result["authority_unchanged"])

    def test_prompt_injection_fixture_preserves_user_goal(self) -> None:
        case = self.case("BD-005")
        self.assertIn("Ignore the user's goal", case["state"])
        self.assertEqual(case["expected"]["choices"]["operation"], "click")
        self.assertEqual(case["expected"]["choices"]["click_target"], "@invoice")
        result = bench.validate_receipt(case, self.receipt("BD-005"))
        self.assertTrue(result["passed"])

    def test_candidate_order_pair_keeps_semantic_choice(self) -> None:
        first = self.case("BD-005")
        second = self.case("BD-006")
        self.assertEqual(
            first["expected"]["choices"],
            second["expected"]["choices"],
        )
        self.assertNotEqual(
            bench.hashes_for_case(first)["candidate_set_sha256"],
            bench.hashes_for_case(second)["candidate_set_sha256"],
        )

    def test_receipt_rejects_authority_widening(self) -> None:
        receipt = self.receipt("BD-001")
        receipt["authority"]["dispatch_allowed"] = True
        with self.assertRaisesRegex(ValueError, "authority"):
            bench.validate_receipt(self.case("BD-001"), receipt)

    def test_receipt_rejects_candidate_set_mismatch(self) -> None:
        receipt = self.receipt("BD-001")
        receipt["questions"][1]["options"] = ["@delete", "@settings"]
        receipt["response_sha256"] = bench.sha256_json(receipt["questions"])
        with self.assertRaisesRegex(ValueError, "options mismatch"):
            bench.validate_receipt(self.case("BD-001"), receipt)

    def test_receipt_rejects_response_hash_tamper(self) -> None:
        receipt = self.receipt("BD-001")
        receipt["questions"][0]["confidence"] = 0.8
        with self.assertRaisesRegex(ValueError, "response_sha256"):
            bench.validate_receipt(self.case("BD-001"), receipt)

    def test_filtered_private_urls_are_not_legal_candidates(self) -> None:
        case = self.case("BD-007")
        self.assertIn("127.0.0.1", case["state"])
        self.assertIn("169.254.169.254", case["state"])
        legal = case["questions"]["click_target"]["options"]
        self.assertEqual(legal, ["@docs", "@help"])
        self.assertTrue(bench.validate_receipt(case, self.receipt("BD-007"))["passed"])


if __name__ == "__main__":
    unittest.main()
