"""The evaluation worktree is a containment boundary, so test the boundary.

A path-string screen is not containment. Every check here corresponds to a way
the boundary was actually breached or could be: a symlink whose header path sits
comfortably inside the worktree while its target is anywhere on the host, and an
evidence record that reported the refusal without saying which check refused it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from test_realtask_support import (
    AUTO_INGEST_TEST_GEN,
    TASKS_ROOT,
    HarnessTestCase,
    ScriptedResponse,
    load_task_by_id,
    patch_reply,
)

#: Written by the linked module at import time. Its appearance would mean code
#: outside the worktree ran, which is the whole thing being tested.
MARKER_NAME = "outside-code-ran"


def symlink_patch(target) -> str:
    return (
        "diff --git a/_realtask_tests/test_linked.py "
        "b/_realtask_tests/test_linked.py\n"
        "new file mode 120000\n"
        "--- /dev/null\n"
        "+++ b/_realtask_tests/test_linked.py\n"
        "@@ -0,0 +1 @@\n"
        "+{}\n".format(target)
    )


class SymlinkEscapeTests(HarnessTestCase):
    """The test_generation fixture is the one with a writable prefix, so it is
    the one where a candidate can add a file the screen has never seen."""

    def outside_module(self) -> Path:
        """A real .py file outside the worktree, with an import side effect.

        It has to actually exist: a dangling link would be collected as nothing
        and the non-execution assertion below would pass vacuously.
        """
        target = self.tmp / "outside.py"
        target.write_text(
            "import pathlib\n"
            "pathlib.Path({!r}).write_text('ran')\n"
            "\n"
            "def test_linked_module_was_imported():\n"
            "    assert True\n".format(str(self.tmp / MARKER_NAME)),
            encoding="utf-8",
        )
        return target

    def clear_marker(self) -> None:
        marker = self.tmp / MARKER_NAME
        if marker.exists():
            marker.unlink()

    def marker_exists(self) -> bool:
        return (self.tmp / MARKER_NAME).exists()

    def test_a_symlink_patch_is_refused(self):
        self.clear_marker()
        _task, result = self.run_stages(
            AUTO_INGEST_TEST_GEN,
            [patch_reply(symlink_patch(self.outside_module()))],
            ["single"],
        )
        metrics = result.attempts[0].metrics
        self.assertFalse(metrics.patch.applied)
        self.assertEqual(metrics.outcome.value, "INVALID_PATCH")

    def test_the_evidence_says_the_screen_refused_it(self):
        """A containment refusal must not be readable as a parse error."""
        _task, result = self.run_stages(
            AUTO_INGEST_TEST_GEN,
            [patch_reply(symlink_patch(self.outside_module()))],
            ["single"],
        )
        metrics = result.attempts[0].metrics
        self.assertFalse(
            metrics.patch.safety_ok,
            "evidence reported the safety screen as clean for a patch it "
            "refused",
        )
        self.assertIn("120000", metrics.patch.safety_reason)
        self.assertIn("non-regular", metrics.patch.safety_reason)

    def test_a_real_run_leaves_no_symlink_to_execute(self):
        """The end-to-end property: nothing in the worktree points outward.

        Asserting "the outside module did not run" is not enough on its own --
        it also passes when the run never got as far as collecting the
        directory, which is vacuous. What must hold unconditionally is that the
        worktree contains no link out at all.
        """
        task = load_task_by_id(AUTO_INGEST_TEST_GEN, TASKS_ROOT)
        runner = self.runner([patch_reply(symlink_patch(self.outside_module()))])
        runner.run_task(task, ["single"])
        try:
            worktrees = list(runner._worktrees)
            self.assertTrue(worktrees, "no evaluation worktree was created")
            for worktree in worktrees:
                links = [q for q in worktree.root.rglob("*") if q.is_symlink()]
                self.assertEqual(
                    links, [],
                    "a symlink pointing outside the worktree survived a run",
                )
        finally:
            runner.close()

    def test_the_outside_module_is_importable_so_the_check_is_not_vacuous(self):
        """Prove the linked file really would run, or the test above is theatre.

        pytest imports what it collects, so a ``test_*.py`` symlink to a Python
        file executes that file's module body at collection time. This asserts
        the mechanism in isolation, so that "no symlink exists" is known to be
        doing work rather than passing because nothing would have happened
        anyway.
        """
        import subprocess
        import tempfile

        self.clear_marker()
        outside = self.outside_module()
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp) / "_realtask_tests"
            sandbox.mkdir()
            (sandbox / "test_linked.py").symlink_to(outside)
            subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 str(sandbox)],
                capture_output=True, text=True, timeout=120,
                cwd=tmp,
            )
        self.assertTrue(
            self.marker_exists(),
            "the linked module was not imported, so the non-execution check "
            "proves nothing",
        )

    def test_no_symlink_survives_in_any_evaluation_worktree(self):
        """Belt and braces: even if the screen were bypassed, nothing links out."""
        import tempfile

        from realtask.binding import verify_source_binding
        from realtask.evaluation import EvaluationWorktree

        task = self.task(AUTO_INGEST_TEST_GEN)
        binding = verify_source_binding(task)
        with tempfile.TemporaryDirectory() as tmp:
            worktree = EvaluationWorktree.create(task, Path(tmp) / "runs")
            try:
                worktree.apply_patch(symlink_patch(self.outside_module()))
                links = [
                    p for p in worktree.root.rglob("*") if p.is_symlink()
                ]
                self.assertEqual(links, [], "a symlink was created in the worktree")
            finally:
                worktree.close()
        self.assertTrue(binding is not None)


if __name__ == "__main__":
    unittest.main()
