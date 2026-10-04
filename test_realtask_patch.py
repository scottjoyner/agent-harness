"""Patch extraction, safety screening, and taxonomy mapping.

The harness never lets a candidate edit the authoritative source. These tests
pin the boundary: what a candidate patch may touch, how a bad patch is
classified, and the fact that a trailing newline lost to JSON round-tripping is
not mistaken for a malformed diff.
"""
from __future__ import annotations

import unittest

from test_realtask_support import (
    AUTO_INGEST_BUG_FIX,
    MALFORMED_PATCH,
    NON_APPLYING_PATCH,
    REFERENCE_REPAIR,
    HarnessTestCase,
)

from realtask.evaluation import (
    EvaluationWorktree,
    ProgramPolicy,
    WorktreeGuardError,
    render_command,
)
from realtask.patch import extract_patch, normalize_patch, parse_patch_paths, screen_patch

KNOWN = ("auto_ingest/shorts/cli.py", "auto_ingest/shorts/planner.py")


class PatchExtractionTests(unittest.TestCase):
    def test_fenced_diff(self):
        text = "Sure thing:\n```diff\n" + REFERENCE_REPAIR + "```\nHope that helps."
        extracted = extract_patch(text)
        self.assertEqual(extracted.strategy, "fenced_diff")
        self.assertIn("plan_shorts", extracted.text)

    def test_diff_git_region_without_a_fence(self):
        extracted = extract_patch("Here is my patch.\n" + REFERENCE_REPAIR)
        self.assertEqual(extracted.strategy, "diff_git_region")

    def test_bare_unified_diff(self):
        bare = "--- a/auto_ingest/shorts/cli.py\n+++ b/auto_ingest/shorts/cli.py\n@@ -1 +1 @@\n-a\n+b\n"
        extracted = extract_patch("prose\n" + bare)
        self.assertEqual(extracted.strategy, "bare_unified_diff")

    def test_no_diff_at_all(self):
        extracted = extract_patch("I would move the call. No diff provided.")
        self.assertTrue(extracted.is_empty)
        self.assertEqual(extracted.strategy, "none")

    def test_empty_input(self):
        self.assertTrue(extract_patch("").is_empty)
        self.assertEqual(extract_patch("").strategy, "empty")

    def test_normalize_restores_the_trailing_newline(self):
        """git apply calls a diff with no final newline a corrupt patch."""
        stripped = REFERENCE_REPAIR.rstrip("\n")
        self.assertTrue(REFERENCE_REPAIR.endswith("\n"))
        normalized = normalize_patch(stripped)
        self.assertTrue(normalized.endswith("\n"))
        self.assertFalse(normalized.endswith("\n\n\n"))

    def test_parse_paths_strips_prefixes(self):
        paths = parse_patch_paths(
            "--- a/auto_ingest/shorts/cli.py\n+++ b/auto_ingest/shorts/cli.py\n"
        )
        self.assertEqual(paths, {"auto_ingest/shorts/cli.py"})


class PatchSafetyTests(unittest.TestCase):
    def test_known_file_is_allowed(self):
        report = screen_patch(REFERENCE_REPAIR, KNOWN)
        self.assertTrue(report.ok, report.reason)
        self.assertEqual(report.paths, ("auto_ingest/shorts/cli.py",))

    def test_undeclared_file_is_refused(self):
        patch = (
            "diff --git a/auto_ingest/shorts/other.py b/auto_ingest/shorts/other.py\n"
            "--- a/auto_ingest/shorts/other.py\n+++ b/auto_ingest/shorts/other.py\n"
            "@@ -1 +1 @@\n-a\n+b\n"
        )
        report = screen_patch(patch, KNOWN)
        self.assertFalse(report.ok)
        self.assertIn("auto_ingest/shorts/other.py", report.unknown_paths)

    def test_writable_prefix_permits_a_new_test_file(self):
        patch = (
            "diff --git a/_realtask_tests/test_new.py b/_realtask_tests/test_new.py\n"
            "new file mode 100644\n--- /dev/null\n+++ b/_realtask_tests/test_new.py\n"
            "@@ -0,0 +1 @@\n+pass\n"
        )
        self.assertFalse(screen_patch(patch, KNOWN).ok)
        self.assertTrue(screen_patch(patch, KNOWN, ("_realtask_tests/",)).ok)

    def test_parent_traversal_is_refused(self):
        patch = (
            "diff --git a/../../etc/passwd b/../../etc/passwd\n"
            "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-a\n+b\n"
        )
        report = screen_patch(patch, KNOWN)
        self.assertFalse(report.ok)
        self.assertTrue(report.outside_worktree)

    def test_absolute_path_is_refused(self):
        patch = (
            "diff --git a//tmp/x.py b//tmp/x.py\n"
            "--- a//tmp/x.py\n+++ b//tmp/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        )
        self.assertFalse(screen_patch(patch, KNOWN).ok)

    def test_binary_patch_is_refused(self):
        self.assertFalse(screen_patch("GIT binary patch\nliteral 6\n", KNOWN).ok)

    def test_rename_is_refused(self):
        self.assertFalse(screen_patch("rename from a.py\nrename to b.py\n", KNOWN).ok)

    def test_prose_with_no_paths_is_refused(self):
        self.assertFalse(screen_patch("just some prose\n", KNOWN).ok)

    def test_symlink_creation_is_refused(self):
        """A path inside the worktree is not containment if it is a symlink.

        The header path is comfortably relative and has no ``..``, so a
        path-string screen waves it through -- and the acceptance tier then
        imports whatever the link points at, at collection time, outside the
        worktree and outside the model's read-only guarantee.
        """
        patch = (
            "diff --git a/_realtask_tests/test_linked.py "
            "b/_realtask_tests/test_linked.py\n"
            "new file mode 120000\n"
            "--- /dev/null\n"
            "+++ b/_realtask_tests/test_linked.py\n"
            "@@ -0,0 +1 @@\n"
            "+/etc/passwd\n"
        )
        report = screen_patch(patch, KNOWN, ("_realtask_tests/",))
        self.assertFalse(report.ok)
        self.assertIn("120000", report.reason or "")
        self.assertIn(0o120000, report.modes)

    def test_submodule_gitlink_is_refused(self):
        patch = (
            "diff --git a/vendor b/vendor\n"
            "new file mode 160000\n"
            "--- /dev/null\n"
            "+++ b/vendor\n"
            "@@ -0,0 +1 @@\n"
            "+Subproject commit deadbeef\n"
        )
        report = screen_patch(patch, KNOWN, ("_realtask_tests/",))
        self.assertFalse(report.ok)
        self.assertIn(0o160000, report.modes)

    def test_symlink_hidden_in_an_allowed_prefix_is_still_refused(self):
        """The writable prefix must not become the escape hatch."""
        patch = (
            "diff --git a/_realtask_tests/ok.py b/_realtask_tests/ok.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/_realtask_tests/ok.py\n"
            "@@ -0,0 +1 @@\n"
            "+assert True\n"
            "diff --git a/_realtask_tests/evil.py b/_realtask_tests/evil.py\n"
            "new file mode 120000\n"
            "--- /dev/null\n"
            "+++ b/_realtask_tests/evil.py\n"
            "@@ -0,0 +1 @@\n"
            "+/etc/passwd\n"
        )
        self.assertFalse(screen_patch(patch, KNOWN, ("_realtask_tests/",)).ok)

    def test_a_regular_new_file_is_still_allowed(self):
        patch = (
            "diff --git a/_realtask_tests/test_new.py b/_realtask_tests/test_new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/_realtask_tests/test_new.py\n"
            "@@ -0,0 +1 @@\n"
            "+def test_x():\n"
            "+    assert True\n"
        )
        report = screen_patch(patch, KNOWN, ("_realtask_tests/",))
        self.assertTrue(report.ok, report.reason)
        self.assertIn(0o100644, report.modes)

    def test_mode_screening_does_not_reject_an_executable_script(self):
        patch = (
            "diff --git a/_realtask_tests/run.sh b/_realtask_tests/run.sh\n"
            "new file mode 100755\n"
            "--- /dev/null\n"
            "+++ b/_realtask_tests/run.sh\n"
            "@@ -0,0 +1 @@\n"
            "+#!/bin/sh\n"
        )
        self.assertTrue(screen_patch(patch, KNOWN, ("_realtask_tests/",)).ok)


class ProgramPolicyTests(HarnessTestCase):
    def test_python_is_always_allowed(self):
        policy = ProgramPolicy()
        ok, reason = policy.permits("python3")
        self.assertTrue(ok, reason)

    def test_unlisted_program_is_refused(self):
        policy = ProgramPolicy()
        ok, reason = policy.permits("curl")
        self.assertFalse(ok)
        self.assertIn("allow list", reason)

    def test_shell_is_refused(self):
        policy = ProgramPolicy()
        self.assertFalse(policy.permits("bash")[0])
        self.assertFalse(policy.permits("sh")[0])

    def test_fixture_can_widen_the_list_explicitly(self):
        policy = ProgramPolicy(["git"])
        self.assertTrue(policy.permits("git")[0])

    def test_placeholders_are_substituted(self):
        worktree = self.tmp / "wt"
        rendered = render_command(
            ["${PYTHON}", "-m", "pytest", "${WORKTREE}/t", "${ANSWER}"], worktree
        )
        self.assertEqual(rendered[0], __import__("sys").executable)
        self.assertEqual(rendered[3], str(worktree) + "/t")
        self.assertEqual(rendered[4], str(worktree) + "/_realtask_answer.txt")


class WorktreeGuardTests(HarnessTestCase):
    def test_worktree_inside_the_bound_source_is_refused(self):
        """The harness refuses to build an evaluation copy inside read-only trees."""
        task = self.task(AUTO_INGEST_BUG_FIX)
        with self.assertRaises(WorktreeGuardError):
            EvaluationWorktree.create(task, task.source_dir / "eval")

    def test_worktree_inside_the_fixture_is_refused(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        with self.assertRaises(WorktreeGuardError):
            EvaluationWorktree.create(task, task.root / "work")

    def test_worktree_outside_is_allowed(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        wt = EvaluationWorktree.create(task, self.tmp / "work")
        self.addCleanup(wt.close)
        self.assertTrue(wt.root.is_dir())
        self.assertTrue((wt.root / "auto_ingest" / "shorts" / "cli.py").is_file())


class ApplyTaxonomyTests(HarnessTestCase):
    def worktree(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        wt = EvaluationWorktree.create(task, self.tmp / "work")
        self.addCleanup(wt.close)
        return task, wt

    def test_reference_repair_applies_and_changes_exactly_one_file(self):
        task, wt = self.worktree()
        result = wt.apply_patch(REFERENCE_REPAIR)
        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(result.changed_files, ("auto_ingest/shorts/cli.py",))
        self.assertEqual(result.applier, "git")

    def test_malformed_patch_is_invalid_patch(self):
        _task, wt = self.worktree()
        result = wt.apply_patch(MALFORMED_PATCH)
        self.assertFalse(result.ok)
        self.assertEqual(result.outcome_hint, "INVALID_PATCH")

    def test_well_formed_but_unapplicable_patch_is_distinguished(self):
        _task, wt = self.worktree()
        result = wt.apply_patch(NON_APPLYING_PATCH)
        self.assertFalse(result.ok)
        self.assertEqual(result.outcome_hint, "PATCH_DOES_NOT_APPLY")

    def test_a_patch_missing_its_trailing_newline_still_applies(self):
        """Regression guard: JSON round-tripping must not fake INVALID_PATCH."""
        _task, wt = self.worktree()
        result = wt.apply_patch(REFERENCE_REPAIR.rstrip("\n"))
        self.assertTrue(result.ok, result.stderr)

    def test_applying_a_patch_twice_is_reported_not_silently_ok(self):
        _task, wt = self.worktree()
        self.assertTrue(wt.apply_patch(REFERENCE_REPAIR).ok)
        second = wt.apply_patch(REFERENCE_REPAIR)
        self.assertFalse(second.ok)
        self.assertEqual(second.outcome_hint, "PATCH_DOES_NOT_APPLY")

    def test_rejected_patch_leaves_the_worktree_untouched(self):
        task, wt = self.worktree()
        before = wt.hashes()
        wt.apply_patch(NON_APPLYING_PATCH)
        self.assertEqual(wt.hashes(), before)

    def test_syntax_check_catches_broken_python(self):
        _task, wt = self.worktree()
        target = wt.root / "auto_ingest" / "shorts" / "cli.py"
        target.write_text(target.read_text() + "\ndef broken(:\n", encoding="utf-8")
        ok, errors = wt.syntax(["auto_ingest/shorts/cli.py"])
        self.assertFalse(ok)
        self.assertIn("auto_ingest/shorts/cli.py", errors)


class CommandPolicyTests(HarnessTestCase):
    def test_unlisted_program_is_never_executed(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        wt = EvaluationWorktree.create(task, self.tmp / "work")
        self.addCleanup(wt.close)
        result = wt.run_command(["curl", "http://example.invalid"])
        self.assertFalse(result.allowed)
        self.assertFalse(result.passed)
        self.assertIn("allow list", result.rejection)

    def test_empty_argv_is_rejected(self):
        task = self.task(AUTO_INGEST_BUG_FIX)
        wt = EvaluationWorktree.create(task, self.tmp / "work")
        self.addCleanup(wt.close)
        result = wt.run_command([])
        self.assertFalse(result.allowed)


if __name__ == "__main__":
    unittest.main()
