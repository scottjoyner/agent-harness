"""Shared helpers for the real-task benchmark test suite.

Follows the existing repository convention: stdlib ``unittest``, no pytest
fixtures, no third-party imports, runnable from the repo root with
``python3 -m unittest test_realtask -v``.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent
TASKS_ROOT = REPO_ROOT / "realtask" / "tasks"
AUTO_INGEST_BUG_FIX = "auto_ingest_plan_shorts_live_driver"
AUTO_INGEST_CONTRACT = "auto_ingest_plan_shorts_contract"
AUTO_INGEST_REVIEW = "auto_ingest_shorts_plan_review"
AUTO_INGEST_TEST_GEN = "auto_ingest_driver_lifetime_regression_test"
AUTO_INGEST_REFACTOR = "auto_ingest_shorts_driver_helper"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from realtask.adapter import ScriptedAdapter, ScriptedResponse  # noqa: E402
from realtask.evidence import RunDirectory  # noqa: E402
from realtask.fixtures import load_task_by_id  # noqa: E402
from realtask.runner import BenchmarkRunner, RunnerOptions  # noqa: E402
from realtask.taxonomy import Outcome  # noqa: E402

#: The canonical repair for the campaign fixture: planning moves inside the
#: ``try`` so the driver is still live, and the ``finally`` close is retained so
#: discussion-mode cleanup and the exception path still close it exactly once.
REFERENCE_REPAIR = """diff --git a/auto_ingest/shorts/cli.py b/auto_ingest/shorts/cli.py
--- a/auto_ingest/shorts/cli.py
+++ b/auto_ingest/shorts/cli.py
@@ -64,15 +64,16 @@ def _cmd_plan(args) -> int:
         else:
             brief = curator.curate_brief(driver, args.topic, top_papers=args.top_papers)
         anchors = backdrop.select_highway_pool(driver, limit=args.pool)
+        # Plan while the driver is still live: plan_shorts mines real graph
+        # content through it (reveal + myth/fact). Planning after close()
+        # silently degrades to templated text (S-G10).
+        plan = planner.plan_shorts(
+            brief, anchors, short_count=args.shorts, short_dur=args.dur,
+            shots_per_short=args.shots_per_short, seed=args.seed,
+            driver=driver,
+        )
     finally:
         driver.close()
-    # Pass the live driver so real graph content is mined (reveal + myth/fact)
-    # instead of falling back to templated text (S-G10).
-    plan = planner.plan_shorts(
-        brief, anchors, short_count=args.shorts, short_dur=args.dur,
-        shots_per_short=args.shots_per_short, seed=args.seed,
-        driver=driver,
-    )
     out = args.plans_dir or DEFAULT_PLANS_DIR
     path = Path(out) / f"{args.topic}__{plan.plan_id}.json"
     plan.save(path)
"""

#: A well-formed diff whose context does not exist in the frozen snapshot.
NON_APPLYING_PATCH = """diff --git a/auto_ingest/shorts/cli.py b/auto_ingest/shorts/cli.py
--- a/auto_ingest/shorts/cli.py
+++ b/auto_ingest/shorts/cli.py
@@ -1 +1 @@
-this context line does not exist in the frozen snapshot
+replacement
"""

MALFORMED_PATCH = "I would fix the driver by moving the call. Here is my plan: <<>>>\n"


#: A genuine behaviour-preserving refactor of the small_refactor fixture: one
#: ``@contextmanager`` helper owns the driver lifetime and all seven handlers go
#: through it. Used to prove that fixture is satisfiable rather than merely
#: strict. Stored as a file so it stays reviewable.
REFERENCE_REFRACTOR_PATH = REPO_ROOT / "test_realtask_reference_refactor.diff"

#: A careless repair: it fixes the driver-lifetime ordering *and* quietly changes
#: the plans-directory fallback and the --pool default. Targeted acceptance passes;
#: the broader acceptance suite catches it. Used to prove REGRESSION_FAILURE is
#: reachable and correctly classified.
REFERENCE_REGRESSION_PATH = REPO_ROOT / "test_realtask_reference_regression.diff"


def reference_refactor() -> str:
    return REFERENCE_REFRACTOR_PATH.read_text(encoding="utf-8")


def regression_on_repair() -> str:
    return REFERENCE_REGRESSION_PATH.read_text(encoding="utf-8")


def patch_reply(patch: str = REFERENCE_REPAIR, confidence: float = 0.9) -> ScriptedResponse:
    return ScriptedResponse(
        content=json.dumps(
            {
                "patch": patch,
                "tests": ["assert the driver is live during planning"],
                "assumptions": ["the finally close is retained"],
                "confidence": confidence,
            }
        )
    )


def scout_reply() -> ScriptedResponse:
    return ScriptedResponse(
        content=json.dumps(
            {
                "root_cause": (
                    "In auto_ingest/shorts/cli.py the plan subcommand closes the driver in a "
                    "finally block that runs before planner.plan_shorts is called, so planning "
                    "receives an already-closed driver and content mining silently falls back "
                    "to templated text."
                ),
                "relevant_files": ["auto_ingest/shorts/cli.py"],
                "plan": [
                    "move the plan_shorts call inside the try block",
                    "retain the finally: driver.close() so cleanup stays correct",
                ],
                "risks": ["the driver leaks if planning raises"],
                "confidence": 0.8,
            }
        )
    )


def analysis_reply() -> ScriptedResponse:
    return ScriptedResponse(
        content=json.dumps(
            {
                "root_cause": (
                    "auto_ingest/shorts/cli.py closes the driver in a finally block and then "
                    "calls plan_shorts with the already-closed driver. The content mining in "
                    "planner.plan_shorts is wrapped in a blanket except, so the command still "
                    "exits 0 and silently falls back to templated text. A correct repair must "
                    "keep the finally close so --discusses cleanup and the exception path still "
                    "close the driver exactly once."
                ),
                "relevant_files": ["auto_ingest/shorts/cli.py"],
                "plan": ["plan while the driver is still live", "retain the finally close"],
                "risks": ["leaking the driver when planning raises"],
                "confidence": 0.85,
            }
        )
    )


def review_reply(verdict: str = "accept", defects: Optional[Sequence[Dict[str, str]]] = None) -> ScriptedResponse:
    return ScriptedResponse(
        content=json.dumps(
            {
                "defects": list(defects or []),
                "missing_coverage": ["no automated coverage of the driver lifetime"],
                "contract_violations": [],
                "verdict": verdict,
                "confidence": 0.85,
            }
        )
    )


def empty_reply() -> ScriptedResponse:
    return ScriptedResponse(content="   \n  \n")


def seal_quietly(root: Path) -> None:
    """Re-seal a fixture copy after mutating its manifest."""
    import contextlib
    import io

    import seal_realtask_fixture
    from realtask.fixtures import load_task

    with contextlib.redirect_stdout(io.StringIO()):
        seal_realtask_fixture.seal(root, "2026-10-02T00:00:00Z", write=True)
    load_task(Path(root) / "task.json")


def new_file_patch(rel: str, body: str) -> str:
    """Build a well-formed 'new file' unified diff for ``rel``."""
    lines = body.splitlines(keepends=True)
    hunk = "@@ -0,0 +1,{} @@\n".format(len(lines)) + "".join("+" + ln for ln in lines)
    return (
        "diff --git a/{rel} b/{rel}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/{rel}\n".format(rel=rel)
    ) + hunk


class HarnessTestCase(unittest.TestCase):
    """Base class giving each test a private temp dir and a run directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="realtask-test-")
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = RunDirectory(self.tmp / "runs", "unittest")

    def task(self, task_id: str):
        return load_task_by_id(task_id, TASKS_ROOT)

    def runner(
        self,
        responses: Sequence[ScriptedResponse],
        *,
        options: Optional[RunnerOptions] = None,
        **runner_kwargs,
    ) -> BenchmarkRunner:
        adapter = ScriptedAdapter(responses)
        runner = BenchmarkRunner(
            adapter,
            self.run_dir,
            options or RunnerOptions(test_timeout_s=180.0),
            work_root=self.tmp / "work",
            harness_root=REPO_ROOT,
            **runner_kwargs,
        )
        self.addCleanup(runner.close)
        return runner

    def run_stages(self, task_id: str, responses, stages, **kwargs):
        task = self.task(task_id)
        runner = self.runner(responses, **kwargs.pop("runner", {}))
        return task, runner.run_task(task, stages, **kwargs)

    @staticmethod
    def seal_quietly(root: Path) -> None:
        import contextlib
        import io

        import seal_realtask_fixture
        from realtask.fixtures import load_task

        with contextlib.redirect_stdout(io.StringIO()):
            seal_realtask_fixture.seal(root, "2026-10-02T00:00:00Z", write=True)
        load_task(Path(root) / "task.json")

    def tree_fingerprint(self, task) -> Dict[str, str]:
        import hashlib

        out: Dict[str, str] = {}
        for base in (task.source_dir, task.tests_dir):
            for path in sorted(base.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    out[path.relative_to(task.root).as_posix()] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
        return out

    def git(self, *args: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        import os

        env = dict(os.environ)
        env.update(
            GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@invalid",
            GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@invalid",
        )
        argv = ["git"] + list(args)
        return subprocess.run(
            argv, cwd=(str(cwd) if cwd is not None else None),
            capture_output=True, text=True, timeout=60, env=env,
        )

    def assertOutcome(self, state, expected: Outcome) -> None:
        self.assertEqual(
            state.metrics.outcome,
            expected,
            "notes={} outcomes_seen={}".format(
                list(state.metrics.notes), [o.value for o in state.metrics.outcomes_seen]
            ),
        )

    def run_json(self, *args: str) -> Dict[str, Any]:
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / "realtime_bench.py"), *args],
            capture_output=True, text=True, timeout=300, cwd=str(REPO_ROOT),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)


def evidence_files(run_dir: RunDirectory) -> List[str]:
    return sorted(
        str(p.relative_to(run_dir.path))
        for p in run_dir.path.rglob("*")
        if p.is_file()
    )
