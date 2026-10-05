"""Role contract tests: SCOUT, IMPLEMENTER, REVIEWER.

The schemas are the contract between the harness and a model. They must be
strict about the fields they require and tolerant about formatting, and they
must never require hidden reasoning.
"""
from __future__ import annotations

import json
import unittest

from test_realtask_support import REPO_ROOT  # noqa: F401  (path bootstrap)

from realtask.roles import (
    SCHEMAS,
    ImplementerResult,
    ReviewVerdict,
    ReviewerResult,
    Role,
    RoleProtocolError,
    ScoutResult,
    extract_json_object,
    refinement_prompt,
    reviewer_prompt,
    schema_block,
    scout_prompt,
    single_prompt,
)


class RoleSchemaTests(unittest.TestCase):
    def test_all_three_roles_are_declared(self):
        self.assertEqual(
            set(SCHEMAS), {Role.SCOUT, Role.IMPLEMENTER, Role.REVIEWER}
        )

    def test_scout_contract(self):
        for field in ("root_cause", "relevant_files", "plan", "risks", "confidence"):
            self.assertIn(field, SCHEMAS[Role.SCOUT])

    def test_implementer_contract(self):
        for field in ("patch", "tests", "assumptions", "confidence"):
            self.assertIn(field, SCHEMAS[Role.IMPLEMENTER])

    def test_reviewer_contract(self):
        for field in (
            "defects", "missing_coverage", "contract_violations", "verdict", "confidence",
        ):
            self.assertIn(field, SCHEMAS[Role.REVIEWER])

    def test_contracts_do_not_ask_for_hidden_reasoning(self):
        banned = ("chain of thought", "reasoning trace", "think step by step",
                  "scratchpad", "internal monologue", "hidden")
        for role, spec in SCHEMAS.items():
            blob = json.dumps(spec).lower()
            for phrase in banned:
                self.assertNotIn(phrase, blob, "{} contract asks for {}".format(role, phrase))

    def test_schema_block_is_non_empty_for_every_role(self):
        for role in SCHEMAS:
            self.assertTrue(schema_block(role).strip())


class JsonExtractionTests(unittest.TestCase):
    def test_plain_object(self):
        payload, truncated = extract_json_object('{"a": 1}')
        self.assertEqual(payload, {"a": 1})
        self.assertFalse(truncated)

    def test_fenced_object(self):
        text = 'Here you go:\n```json\n{"a": 2}\n```\nthanks'
        payload, _ = extract_json_object(text)
        self.assertEqual(payload, {"a": 2})

    def test_object_containing_braces_and_strings(self):
        payload, _ = extract_json_object('prefix {"patch": "a } b { c", "n": 1} suffix')
        self.assertEqual(payload["patch"], "a } b { c")

    def test_truncated_object_is_flagged(self):
        payload, truncated = extract_json_object('{"root_cause": "half a sen')
        self.assertIsNone(payload)
        self.assertTrue(truncated)

    def test_no_object_at_all(self):
        payload, truncated = extract_json_object("just prose")
        self.assertIsNone(payload)
        self.assertFalse(truncated)


class ScoutParsingTests(unittest.TestCase):
    def valid(self):
        return json.dumps(
            {
                "root_cause": "the driver is closed before planning",
                "relevant_files": ["auto_ingest/shorts/cli.py"],
                "plan": ["move the call inside try"],
                "risks": ["driver leak"],
                "confidence": 0.7,
            }
        )

    def test_valid_scout(self):
        result = ScoutResult.parse(self.valid())
        self.assertEqual(result.root_cause, "the driver is closed before planning")
        self.assertEqual(result.relevant_files, ("auto_ingest/shorts/cli.py",))
        self.assertEqual(result.confidence, 0.7)
        self.assertEqual(result.role, Role.SCOUT)

    def test_missing_root_cause_is_a_protocol_failure(self):
        payload = json.loads(self.valid())
        del payload["root_cause"]
        with self.assertRaises(RoleProtocolError) as ctx:
            ScoutResult.parse(json.dumps(payload))
        self.assertEqual(ctx.exception.outcome_hint, "PROTOCOL_FAILURE")

    def test_missing_confidence_is_rejected(self):
        payload = json.loads(self.valid())
        del payload["confidence"]
        with self.assertRaises(RoleProtocolError):
            ScoutResult.parse(json.dumps(payload))

    def test_out_of_range_confidence_is_rejected(self):
        payload = json.loads(self.valid())
        payload["confidence"] = 1.5
        with self.assertRaises(RoleProtocolError):
            ScoutResult.parse(json.dumps(payload))

    def test_truncated_output_maps_to_truncated_not_protocol_failure(self):
        with self.assertRaises(RoleProtocolError) as ctx:
            ScoutResult.parse(self.valid()[:40])
        self.assertEqual(ctx.exception.outcome_hint, "TRUNCATED")

    def test_round_trip_to_dict(self):
        payload = ScoutResult.parse(self.valid()).to_dict()
        self.assertEqual(payload["role"], "scout")
        self.assertEqual(payload["relevant_files"], ["auto_ingest/shorts/cli.py"])


class ImplementerParsingTests(unittest.TestCase):
    def valid(self):
        return json.dumps(
            {
                "patch": "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
                "tests": ["add one"],
                "assumptions": ["none"],
                "confidence": 0.9,
            }
        )

    def test_valid_implementer(self):
        result = ImplementerResult.parse(self.valid())
        self.assertIn("diff --git", result.patch)
        self.assertEqual(result.tests, ("add one",))
        self.assertEqual(result.confidence, 0.9)

    def test_patch_must_be_a_string(self):
        payload = json.loads(self.valid())
        payload["patch"] = ["not", "a", "string"]
        with self.assertRaises(RoleProtocolError) as ctx:
            ImplementerResult.parse(json.dumps(payload))
        self.assertIn("must be a string", str(ctx.exception))

    def test_missing_patch_is_rejected(self):
        payload = json.loads(self.valid())
        del payload["patch"]
        with self.assertRaises(RoleProtocolError):
            ImplementerResult.parse(json.dumps(payload))

    def test_json_escaped_newlines_decode_to_real_newlines(self):
        """A patch whose newlines arrive JSON-escaped must round-trip intact."""
        raw = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        wire = json.dumps({"patch": raw, "tests": [], "assumptions": [], "confidence": 0.5})
        self.assertIn("\\n", wire, "precondition: the wire form must be escaped")
        self.assertEqual(ImplementerResult.parse(wire).patch, raw)

    def test_double_escaped_patch_is_preserved_verbatim(self):
        """A model that double-escapes is not silently 'fixed'.

        The text is kept exactly as sent so patch extraction can reject it and
        report INVALID_PATCH rather than the harness guessing at intent.
        """
        literal = "diff --git a/x.py b/x.py\\n--- a/x.py\\n"
        result = ImplementerResult.parse(
            json.dumps({"patch": literal, "tests": [], "assumptions": [], "confidence": 0.5})
        )
        self.assertEqual(result.patch, literal)
        from realtask.patch import extract_patch

        self.assertEqual(extract_patch(result.patch).strategy, "diff_git_region")


class ReviewerParsingTests(unittest.TestCase):
    def valid(self, verdict="accept"):
        return json.dumps(
            {
                "defects": [
                    {"severity": "high", "location": "cli.py:71", "description": "closed driver"}
                ],
                "missing_coverage": ["no lifecycle test"],
                "contract_violations": [],
                "verdict": verdict,
                "confidence": 0.8,
            }
        )

    def test_accept(self):
        result = ReviewerResult.parse(self.valid("accept"))
        self.assertIs(result.verdict, ReviewVerdict.ACCEPT)
        self.assertEqual(len(result.defects), 1)
        self.assertEqual(result.defects[0].severity, "high")

    def test_revise(self):
        result = ReviewerResult.parse(self.valid("revise"))
        self.assertIs(result.verdict, ReviewVerdict.REVISE)

    def test_verdict_is_case_insensitive_but_exact(self):
        result = ReviewerResult.parse(self.valid("ACCEPT"))
        self.assertIs(result.verdict, ReviewVerdict.ACCEPT)
        for bad in ("approve", "ok", "yes", "", "accept-with-notes"):
            with self.subTest(verdict=bad):
                payload = json.loads(self.valid())
                payload["verdict"] = bad
                with self.assertRaises(RoleProtocolError):
                    ReviewerResult.parse(json.dumps(payload))

    def test_missing_verdict_is_rejected(self):
        payload = json.loads(self.valid())
        del payload["verdict"]
        with self.assertRaises(RoleProtocolError):
            ReviewerResult.parse(json.dumps(payload))

    def test_defect_strings_are_accepted(self):
        payload = json.loads(self.valid())
        payload["defects"] = ["a bare defect description"]
        result = ReviewerResult.parse(json.dumps(payload))
        self.assertEqual(result.defects[0].description, "a bare defect description")
        self.assertEqual(result.defects[0].severity, "unspecified")

    def test_reviewer_renders_findings_for_the_refinement_prompt(self):
        result = ReviewerResult.parse(self.valid("revise"))
        rendered = result.to_json_text()
        self.assertIn("closed driver", rendered)
        self.assertIn("revise", rendered)


class PromptTests(unittest.TestCase):
    def test_role_prompts_declare_no_tool_access(self):
        common = [
            scout_prompt("p", ["c"], ["a"], "SRC", ["auto_ingest/shorts/cli.py"]),
            single_prompt("p", ["c"], ["a"], "SRC", "t", "bug_fix"),
            reviewer_prompt("p", ["c"], "BINDING", "PATCH", "EVIDENCE"),
        ]
        for prompt in common:
            self.assertIn("NO tools", prompt)
            self.assertIn("JSON object", prompt)
            self.assertNotIn("chain of thought", prompt.lower())

    def test_prompts_carry_the_exact_candidate_and_binding(self):
        prompt = reviewer_prompt("p", ["c"], "BINDING sha=deadbeef", "PATCHLINE", "rc=1")
        self.assertIn("BINDING sha=deadbeef", prompt)
        self.assertIn("PATCHLINE", prompt)
        self.assertIn("rc=1", prompt)

    def test_refinement_prompt_carries_the_review_and_old_patch(self):
        review = ReviewerResult.parse(
            json.dumps(
                {
                    "defects": [{"severity": "high", "location": "cli.py", "description": "leak"}],
                    "missing_coverage": [],
                    "contract_violations": [],
                    "verdict": "revise",
                    "confidence": 0.5,
                }
            )
        )
        prompt = refinement_prompt("p", ["c"], ["a"], "SRC", None, "RAW REVIEW", review, "OLD PATCH")
        self.assertIn("ONLY revision round", prompt)
        self.assertIn("leak", prompt)
        self.assertIn("RAW REVIEW", prompt)
        self.assertIn("OLD PATCH", prompt)


if __name__ == "__main__":
    unittest.main()


class ToolCallLabellingTests(unittest.TestCase):
    """A reply is a tool request only when no deliverable came with it.

    Found by running the corpus against a real 3B tool-tuned model, not by
    reading: all four analysis fixtures came back ``TOOL_CALL_REQUESTED``. Three
    were genuinely degenerate -- a ``call_id`` of several hundred repeated
    digits, with no path in them at all, so there was nothing to serve even if
    the harness had a read tool. The fourth was different, and worth the fix.

    It emitted a complete, well-formed scout payload wrapped in a spurious
    ``<tool_call>`` tag. The payload diagnosed a different bug than the
    fixture's, so it would have failed anyway -- but it was filed as
    ``TOOL_CALL_REQUESTED``, "the model asked for a tool", rather than as a
    wrong answer. That is the one number the comparison artifact exists to get
    right, and it was wrong for a reason that had nothing to do with the model.
    """

    PAYLOAD = (
        '{"root_cause": "driver closed before planning", '
        '"relevant_files": ["auto_ingest/shorts/cli.py"], '
        '"plan": ["read the finally block"], "risks": [], "confidence": 0.6}'
    )

    def test_a_tagged_payload_is_not_a_tool_request(self):
        from realtask.roles import Role, delivers_result, requested_tools

        text = "<tool_call>" + self.PAYLOAD + "</tool_call>"
        self.assertTrue(requested_tools(text), "the tag should still be seen")
        self.assertTrue(
            delivers_result(Role.SCOUT, text),
            "a well-formed payload wrapped in a spurious tag is still a payload",
        )

    def test_a_real_tool_request_is_still_a_tool_request(self):
        from realtask.roles import Role, delivers_result

        text = '<tool_call name="read" call_id="call_0856"></tool_call>'
        self.assertFalse(delivers_result(Role.SCOUT, text))

    def test_a_tagged_payload_with_the_wrong_schema_is_not_accepted(self):
        """Recovery must not launder a malformed answer into a graded one."""
        from realtask.roles import Role, delivers_result

        text = '<tool_call>{"answer": "the bug is in the parser"}</tool_call>'
        self.assertFalse(
            delivers_result(Role.SCOUT, text),
            "a payload missing the role's required fields is not a deliverable",
        )

    def test_prose_is_not_a_payload(self):
        from realtask.roles import Role, delivers_result

        self.assertFalse(delivers_result(Role.SCOUT, "I think it is the parser."))

    def test_unrelated_json_is_not_a_payload(self):
        from realtask.roles import Role, delivers_result

        self.assertFalse(delivers_result(Role.SCOUT, '{"result": 42}'))

    def test_an_empty_tool_call_is_not_a_payload(self):
        from realtask.roles import Role, delivers_result

        self.assertFalse(delivers_result(Role.REVIEWER, '<tool_call name="read"/>'))

    def test_it_does_not_weaken_tool_call_detection(self):
        """The degenerate case the fix was *not* for must stay detected.

        This is the important negative. If a model emits a tool call and nothing
        usable, the harness must still say so -- otherwise the fix has quietly
        turned every failure into a graded attempt and made the corpus look
        kinder than it is.
        """
        from realtask.roles import Role, delivers_result

        degenerate = (
            '<tool_call name="read" call_id="call_0856794312a5f0a9c4e0d0'
            + "9" * 400
            + '"></tool_call>'
        )
        self.assertFalse(delivers_result(Role.SCOUT, degenerate))

    def test_every_role_with_a_payload_is_covered(self):
        from realtask.roles import ROLE_RESULT_CLASS, Role, delivers_result

        for role in (Role.SCOUT, Role.IMPLEMENTER, Role.REVIEWER):
            with self.subTest(role=role):
                # A scout payload is not an implementer payload, so each role
                # must reject the other's shape rather than accept anything.
                self.assertTrue(
                    delivers_result(role, _payload_for(role)),
                    "no payload class wired for {}".format(role),
                )
                self.assertFalse(delivers_result(role, "not json at all"))


def _payload_for(role) -> str:
    if role.value == "scout":
        return (
            '{"root_cause": "x", "relevant_files": [], "plan": [], '
            '"risks": [], "confidence": 0.5}'
        )
    if role.value == "implementer":
        return (
            '{"patch": "", "tests": ["t"], "assumptions": ["a"], '
            '"confidence": 0.5}'
        )
    return (
        '{"defects": [], "missing_coverage": [], '
        '"contract_violations": [], "verdict": "accept", "confidence": 0.5}'
    )
