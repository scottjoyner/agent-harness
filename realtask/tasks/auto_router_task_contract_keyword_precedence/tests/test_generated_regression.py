"""Meta-oracle for the ``test_generation`` fixture.

The candidate's job is not to fix the defect. It is to add a regression test
that *would have caught* it. This check therefore does not simply run the new
test -- it proves the new test discriminates:

1. the candidate added at least one new ``test_*.py`` file;
2. that test FAILS against the unrepaired snapshot (it detects the bug);
3. that test PASSES against the snapshot with the canonical repair applied
   (it is not simply failing for an unrelated reason).

This is the one fixture family where candidate-authored code is executed. It
runs inside the harness's disposable worktree, with a scrubbed environment,
and only against a copy of the bound source. See
``docs/REAL-TASK-BENCHMARK.md`` ("Executing candidate-authored code").
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
WORKTREE = TESTS_DIR.parent
SELF = Path(__file__).name

#: The canonical repair, expressed as an anchored edit rather than a diff so the
#: check fails loudly if the frozen snapshot ever stops matching the anchor.
#: The defect is that ``normalize_task_kind`` returns on the first substring
#: match in a fixed priority order, so one incidental keyword decides the entire
#: contract. The repair below lets an explicit code verb win over a softer
#: category; it is one sound resolution, not the only one.
REFERENCE_OLD = '''    keyword_map = [
        ("research", ("research", "investigate", "evidence", "source", "cite", "browse", "lookup")),
        ("analysis", ("analy", "measure", "metrics", "perf", "performance", "compare", "review")),
        ("refinement", ("refine", "rewrite", "polish", "edit", "summarize", "summary")),
        ("operations", ("deploy", "service", "ops", "monitor", "health", "diagnostic")),
        ("code", ("code", "implement", "patch", "bug", "test", "refactor")),
    ]
'''

REFERENCE_NEW = '''    keyword_map = [
        ("code", ("code", "implement", "patch", "bug", "test", "refactor")),
        ("research", ("research", "investigate", "evidence", "source", "cite", "browse", "lookup")),
        ("analysis", ("analy", "measure", "metrics", "perf", "performance", "compare", "review")),
        ("refinement", ("refine", "rewrite", "polish", "edit", "summarize", "summary")),
        ("operations", ("deploy", "service", "ops", "monitor", "health", "diagnostic")),
    ]
'''

CONTRACT = WORKTREE / "auto_router" / "task_contract.py"


def candidate_tests():
    return sorted(
        p for p in TESTS_DIR.glob("test_*.py") if p.name != SELF
    )


def stage(tmp_path: Path, name: str, repaired: bool) -> Path:
    dest = tmp_path / name
    shutil.copytree(
        WORKTREE,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    if repaired:
        target = dest / "auto_router" / "task_contract.py"
        text = target.read_text(encoding="utf-8")
        assert REFERENCE_OLD in text, (
            "frozen snapshot no longer matches the reference-repair anchor; "
            "re-freeze the fixture before trusting this oracle"
        )
        target.write_text(text.replace(REFERENCE_OLD, REFERENCE_NEW), encoding="utf-8")
    return dest


def run_test(root: Path, test_rel: str):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(root)
    for key in list(env):
        upper = key.upper()
        if any(
            marker in upper
            for marker in ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "APIKEY", "_API_KEY")
        ):
            env.pop(key, None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test_rel],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


def test_reference_anchor_is_present():
    text = CONTRACT.read_text(encoding="utf-8")
    assert REFERENCE_OLD in text, "frozen snapshot drifted from the reference repair"


def test_candidate_added_a_regression_test():
    added = candidate_tests()
    assert added, (
        "no new test_*.py was added under {}; a regression test is required".format(
            TESTS_DIR
        )
    )


def test_new_test_detects_the_defect(tmp_path):
    added = candidate_tests()
    assert added, "no candidate regression test to evaluate"
    buggy = stage(tmp_path, "buggy", repaired=False)
    for test in added:
        rel = test.relative_to(TESTS_DIR).as_posix()
        result = run_test(buggy, "_realtask_tests/" + rel)
        assert result.returncode != 0, (
            "{} passed against the unrepaired snapshot, so it does not detect "
            "the keyword-precedence defect".format(rel)
        )


def test_new_test_passes_after_the_reference_repair(tmp_path):
    added = candidate_tests()
    assert added, "no candidate regression test to evaluate"
    fixed = stage(tmp_path, "fixed", repaired=True)
    for test in added:
        rel = test.relative_to(TESTS_DIR).as_posix()
        result = run_test(fixed, "_realtask_tests/" + rel)
        assert result.returncode == 0, (
            "{} still fails after the canonical repair, so it is asserting "
            "something other than the defect:\n{}".format(
                rel, (result.stdout or "")[-3000:] + (result.stderr or "")[-3000:]
            )
        )