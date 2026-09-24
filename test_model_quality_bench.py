from __future__ import annotations

import argparse
import json
import unittest

import model_quality_bench as bench


class ModelQualityBenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = bench.load_json(bench.DEFAULT_MANIFEST)
        bench.validate_manifest(cls.manifest)

    def task(self, task_id: str):
        return next(
            task for task in self.manifest["tasks"]
            if task["id"] == task_id
        )

    def test_manifest_is_non_destructive_and_has_all_core_families(self) -> None:
        self.assertTrue(self.manifest["semantics"]["non_destructive"])
        self.assertTrue(self.manifest["semantics"]["tools_are_simulated"])
        families = {task["family"] for task in self.manifest["tasks"]}
        self.assertTrue({
            "decision_judge",
            "structured_output",
            "repo_work",
            "terminal_agent",
            "long_context_retrieval",
            "summarization",
        }.issubset(families))

    def test_json_exact_validator(self) -> None:
        task = self.task("SO-001")
        response = {
            "ok": True,
            "elapsed_ms": 1,
            "body": {
                "choices": [{
                    "message": {
                        "content": json.dumps(task["validator"]["expected"])
                    }
                }]
            },
        }
        success, outcome = bench.score_task(task, response)
        self.assertTrue(success)
        self.assertEqual(outcome, "exact_json_match")

    def test_tool_validator_never_executes_tool(self) -> None:
        task = self.task("TA-001")
        expected = task["validator"]
        response = {
            "ok": True,
            "elapsed_ms": 1,
            "body": {
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "function": {
                                "name": expected["name"],
                                "arguments": json.dumps(expected["arguments"]),
                            }
                        }],
                    }
                }]
            },
        }
        success, outcome = bench.score_task(task, response)
        self.assertTrue(success)
        self.assertEqual(outcome, "exact_tool_call_match")

    def test_long_context_generation_contains_one_needle(self) -> None:
        task = self.task("LC-001")
        prompt = bench.make_long_context(task, 8192)
        needle = task["long_context"]["needle"]
        self.assertEqual(prompt.count(needle), 1)
        self.assertGreater(len(prompt), 20000)

    def test_reasoning_mode_is_part_of_request_identity(self) -> None:
        task = self.task("DJ-001")
        payload = bench.request_payload(
            task,
            "fixture-model",
            "xhigh",
            0,
        )
        self.assertEqual(payload["reasoning_effort"], "xhigh")

        disabled = bench.request_payload(
            task,
            "fixture-model",
            "none",
            0,
        )
        self.assertEqual(
            disabled["chat_template_kwargs"],
            {"enable_thinking": False},
        )

    def test_validate_only_needs_no_endpoint(self) -> None:
        args = argparse.Namespace(
            manifest=bench.DEFAULT_MANIFEST,
            endpoint=None,
            model=None,
            artifact=None,
            artifact_sha256=None,
            runtime_id=None,
            reasoning_mode="default",
            reasoning_policy_sha256=None,
            context_tokens=0,
            family=[],
            case=[],
            timeout=120,
            validate_only=True,
            output=None,
        )
        result = bench.run(args)
        self.assertEqual(
            result["schema_version"],
            "model-quality-validation-v1",
        )
        self.assertGreater(result["task_count"], 0)


if __name__ == "__main__":
    unittest.main()
