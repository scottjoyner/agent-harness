"""Corpus-level tests: is this actually a benchmark?

Individual fixture tests prove each oracle works. These prove the corpus as a
whole is worth measuring on:

* every fixture is *satisfiable* -- a reference solution reaches SUCCESS, so no
  fixture is a wall rather than a task;
* every fixture is *discriminating* -- the unmodified snapshot fails its targeted
  acceptance, so no fixture passes without work;
* fixtures are *independent* -- they are not five views of one bug;
* the corpus *spans repositories* and *declares its families*;
* no fixture smuggles a secret.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Dict, List, Tuple

from test_realtask_support import AUTO_INGEST_TEST_GEN, REPO_ROOT, TASKS_ROOT, HarnessTestCase

from realtask.fixtures import iter_fixture_manifests, load_source_manifest, load_task
from realtask.taxonomy import Outcome
from realtask.version import SCHEMA_TASK

#: task_id -> the reference solution that must reach SUCCESS. Every patch
#: deliverable needs one, otherwise the fixture is a wall rather than a task.
REFERENCE_SOLUTIONS: Dict[str, str] = {
    "auto_ingest_plan_shorts_live_driver": "test_realtask_reference_live_driver.diff",
    "auto_ingest_shorts_driver_helper": "test_realtask_reference_refactor.diff",
    "auto_router_task_contract_lane_mismatch": "test_realtask_reference_task_contract.diff",
    "auto_router_settings_latency_cache_path": "test_realtask_reference_settings.diff",
    "assistx_answers_store_cursor_drops_ties": "test_realtask_reference_answers_store.diff",
    # test_generation: the deliverable is a *new test file*, so its reference is
    # a new-file diff landing under the fixture's writable prefix.
    "auto_ingest_driver_lifetime_regression_test":
        "test_realtask_reference_regression_test.diff",
}

#: task_id -> the reference answer that must satisfy the grader. An analysis
#: deliverable is prose, so its reference is an answer rather than a diff --
#: without one there is no proof the grader can be satisfied at all.
REFERENCE_ANSWERS: Dict[str, str] = {
    "auto_ingest_shorts_plan_review": "test_realtask_reference_review.json",
    "auto_ingest_plan_shorts_contract": "test_realtask_reference_contract.json",
}

#: A repair of the campaign defect that also changes unrelated defaults. Used to
#: prove the broader tier catches collateral damage, not only the primary defect.
COLLATERAL_DAMAGE: Dict[str, str] = {
    "auto_ingest_plan_shorts_live_driver": "test_realtask_reference_regression.diff",
}

#: A patch that satisfies the oracle by memorising the inputs the oracle names,
#: rather than by fixing the defect. Every one of these must be rejected: a
#: benchmark that cannot tell a fix from a lookup table measures recall of the
#: test file, not engineering.
OVERFIT_PATCHES: Dict[str, str] = {
    "auto_router_settings_latency_cache_path": "test_realtask_overfit_settings.diff",
}

PATCH_TASKS = tuple(REFERENCE_SOLUTIONS)
ANALYSIS_TASKS = tuple(REFERENCE_ANSWERS)
OVERFIT_TASKS = tuple(OVERFIT_PATCHES)


def reference_patch(task_id: str) -> str:
    return (REPO_ROOT / REFERENCE_SOLUTIONS[task_id]).read_text(encoding="utf-8")


def reference_answer(task_id: str) -> str:
    return (REPO_ROOT / REFERENCE_ANSWERS[task_id]).read_text(encoding="utf-8")


def collateral_patch(task_id: str) -> str:
    return (REPO_ROOT / COLLATERAL_DAMAGE[task_id]).read_text(encoding="utf-8")


def targeted_commands(task) -> List[List[str]]:
    return [list(cmd) for cmd in task.acceptance.targeted]


def run_acceptance(task, work_root: Path) -> Tuple[bool, str]:
    """Run a fixture's targeted commands against an untouched worktree copy."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(work_root),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    import os

    merged = dict(os.environ)
    merged.update(env)
    merged.pop("REALTASK_ENDPOINT_BASE_URL", None)
    merged.pop("REALTASK_ENDPOINT_MODEL", None)

    from realtask.evaluation import EvaluationWorktree

    worktree = EvaluationWorktree.create(task, work_root / "runs")
    try:
        results = [worktree.run_command(cmd, timeout_s=300.0)
                   for cmd in targeted_commands(task)]
    finally:
        worktree.close()
    passed = bool(results) and all(r.passed for r in results)
    detail = "\n".join(
        "{} -> rc={} timed_out={}\n{}".format(
            " ".join(r.argv[-3:]), r.returncode, r.timed_out, r.stdout[-1500:]
        )
        for r in results
    )
    return passed, detail


class FixtureInventoryTests(unittest.TestCase):
    def test_every_patch_deliverable_has_a_reference_solution(self):
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            if task.deliverable != "patch":
                continue
            with self.subTest(task=task.task_id):
                self.assertIn(task.task_id, REFERENCE_SOLUTIONS)

    def test_corpus_is_not_empty(self):
        self.assertGreaterEqual(len(iter_fixture_manifests(TASKS_ROOT)), 8)

    def test_every_fixture_is_sealed_and_self_verifying(self):
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            with self.subTest(task=task.task_id):
                load_source_manifest(task)
                self.assertTrue(task.acceptance.targeted)

    def test_corpus_spans_more_than_one_repository(self):
        repositories = {
            load_task(m).source.repository for m in iter_fixture_manifests(TASKS_ROOT)
        }
        self.assertGreaterEqual(
            len(repositories), 2,
            "a corpus drawn from one repository cannot generalise; got {}".format(
                sorted(repositories)
            ),
        )

    def test_every_task_family_is_represented(self):
        families = {
            load_task(m).task_family for m in iter_fixture_manifests(TASKS_ROOT)
        }
        for family in ("bug_fix", "test_generation", "code_review",
                       "small_refactor", "contract_reasoning"):
            self.assertIn(family, families, family)

    def test_no_fixture_bundles_a_secret(self):
        """Look for credential *values*, not credential vocabulary.

        A comment saying "this token as password" is documentation. A field
        assigned a non-empty literal, or a DSN with an inline password, is a
        leak -- so scan for those shapes and not for the words.
        """
        import re

        assigned = re.compile(
            r"""\b(?:password|passwd|secret|api[_-]?key|token|private[_-]?key
                 |credential)\s*[:=]\s*["'][^"']{3,}["']""",
            re.IGNORECASE | re.VERBOSE,
        )
        embedded = re.compile(
            r"""(?:bolt|redis|postgres(?:ql)?|mysql|mongodb)://[^\s:@/]+:[^\s@/]+@
                |\bBearer\s+[A-Za-z0-9._\-]{20,}
                |-----BEGIN [A-Z ]*PRIVATE KEY-----""",
            re.IGNORECASE | re.VERBOSE,
        )
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            blobs = [manifest.read_text(), task.source_manifest_path.read_text()]
            blobs.extend(
                (task.source_dir / rel).read_text(errors="replace")
                for rel in task.source.paths
            )
            joined = "\n".join(blobs)
            with self.subTest(task=task.task_id):
                self.assertIsNone(assigned.search(joined), "credential literal")
                self.assertIsNone(embedded.search(joined), "embedded credential")

    def test_credential_shaped_settings_default_to_empty(self):
        """A frozen settings module must not carry a filled credential default."""
        task = load_task(
            TASKS_ROOT / "auto_router_settings_latency_cache_path" / "task.json"
        )
        text = (task.source_dir / "auto_router" / "settings.py").read_text()
        for field in ("admin_token", "assistx_basic_auth_user",
                      "assistx_basic_auth_pass"):
            with self.subTest(field=field):
                self.assertIn('{}: str = ""'.format(field), text)

    def test_every_patch_task_records_a_known_regression(self):
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            with self.subTest(task=task.task_id):
                self.assertTrue(
                    task.known_regression and len(task.known_regression) > 40,
                    "a fixture without a known regression cannot be used to tell "
                    "a lucky guess from a real fix",
                )

    def test_every_task_declares_constraints(self):
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            with self.subTest(task=task.task_id):
                self.assertGreaterEqual(len(task.constraints), 3)

    def test_task_schemas_are_the_current_one(self):
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            payload = json.loads(manifest.read_text())
            with self.subTest(task=manifest.parent.name):
                self.assertEqual(payload["schema"], SCHEMA_TASK)

    def test_each_fixture_has_a_broader_tier_or_declares_none(self):
        """A missing broader tier is allowed; a broken one is not."""
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            with self.subTest(task=task.task_id):
                for command in task.acceptance.broader:
                    self.assertTrue(command)
                    self.assertTrue(command[0])


class DiscriminatingTests(HarnessTestCase):
    """Every patch fixture must fail on its own untouched snapshot."""

    def test_each_patch_fixture_fails_before_any_change(self):
        for task_id in PATCH_TASKS:
            task = self.task(task_id)
            with self.subTest(task=task_id):
                passed, detail = run_acceptance(
                    task, self.tmp / ("work-" + task_id)
                )
                self.assertFalse(
                    passed,
                    "{} passes its targeted acceptance on the unmodified "
                    "snapshot, so it measures nothing:\n{}".format(task_id, detail),
                )

    def test_each_patch_fixture_fails_through_the_runner(self):
        from test_realtask_support import patch_reply

        for task_id in PATCH_TASKS:
            _task, result = self.run_stages(
                task_id, [patch_reply("I am prose, not a diff.")], ["single"]
            )
            with self.subTest(task=task_id):
                self.assertOutcome(result.attempts[0], Outcome.INVALID_PATCH)

    def test_fixtures_are_not_all_the_same_bug(self):
        """A corpus drawn from one file cannot tell five defects apart.

        Fixtures *within* a campaign deliberately share a snapshot -- that is how
        one real defect gets five independent angles. What must not happen is
        the whole corpus collapsing onto a single source identity.
        """
        identities = set()
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            identities.add(
                (task.source.repository, tuple(sorted(task.source.paths)))
            )
        self.assertGreaterEqual(
            len(identities), 3,
            "corpus has only {} distinct source identity/identities".format(
                len(identities)
            ),
        )

    def test_a_campaign_is_diverse_in_family_and_defect(self):
        """Sharing a snapshot must not mean sharing a task."""
        problems = {}
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            problems.setdefault(
                (task.source.repository, tuple(sorted(task.source.paths))),
                [],
            ).append(task)

        for identity, group in problems.items():
            if len(group) < 2:
                continue
            with self.subTest(identity=identity):
                descriptions = {t.problem for t in group}
                self.assertEqual(
                    len(descriptions), len(group),
                    "fixtures {} describe the same problem".format(
                        sorted(t.task_id for t in group)
                    ),
                )
                self.assertGreaterEqual(
                    len({t.task_family for t in group}), 2,
                    "fixtures {} are all the same family".format(
                        sorted(t.task_id for t in group)
                    ),
                )


class SatisfiableTests(HarnessTestCase):
    """A fixture nothing can pass is a wall, not a benchmark task."""

    def run_reference(self, task_id: str):
        from test_realtask_support import patch_reply

        task, result = self.run_stages(
            task_id, [patch_reply(reference_patch(task_id))], ["single"]
        )
        return task, result

    def test_every_reference_solution_reaches_success(self):
        for task_id in PATCH_TASKS:
            task, result = self.run_reference(task_id)
            state = result.attempts[0]
            with self.subTest(task=task_id):
                self.assertOutcome(state, Outcome.SUCCESS)
                self.assertTrue(state.metrics.patch.applied)
                self.assertTrue(state.metrics.tests.targeted_all_passed)
                self.assertEqual(state.metrics.patch.syntax_ok, True)
                if task.acceptance.broader:
                    self.assertTrue(state.metrics.tests.broader_all_passed)

    def test_every_fixture_with_a_broader_tier_declares_one(self):
        """A 'broader' tier that silently vanishes is worse than none."""
        for task_id in PATCH_TASKS:
            task = self.task(task_id)
            if not task.acceptance.broader:
                continue
            _t, result = self.run_reference(task_id)
            metrics = result.attempts[0].metrics.tests
            with self.subTest(task=task_id):
                self.assertEqual(
                    len(metrics.broader), len(task.acceptance.broader),
                    "the harness did not run the broader tier it was given",
                )

    def test_reference_solutions_stay_inside_the_fixture_sandbox(self):
        """A reference may touch bound source, or its own writable prefix.

        Anything else means the fixture's containment is not actually enforced.
        """
        for task_id in PATCH_TASKS:
            task, result = self.run_reference(task_id)
            changed = set(result.attempts[0].metrics.patch.files_changed)
            allowed = set(task.source.paths)
            for prefix in task.source.writable_prefixes:
                allowed.update(
                    rel for rel in changed if rel.startswith(prefix)
                )
            with self.subTest(task=task_id):
                self.assertLessEqual(
                    changed, allowed,
                    "reference for {} touched {}".format(
                        task_id, sorted(changed - allowed)
                    ),
                )

    def test_test_generation_reference_adds_a_test_and_no_production_change(self):
        """A test-generation reference must not quietly fix the defect."""
        task, result = self.run_reference(AUTO_INGEST_TEST_GEN)
        changed = set(result.attempts[0].metrics.patch.files_changed)
        with self.subTest():
            self.assertOutcome(result.attempts[0], Outcome.SUCCESS)
            self.assertTrue(changed)
            self.assertFalse(
                changed & set(task.source.paths),
                "a test_generation reference edited production source: {}".format(
                    sorted(changed)
                ),
            )
            for path in changed:
                self.assertTrue(path.startswith("_realtask_tests/"), path)
                self.assertTrue(path.endswith(".py"), path)

    def test_reference_solutions_do_not_weaken_the_broader_tier(self):
        """A fixture whose oracle the reference itself fails is malformed."""
        for task_id in PATCH_TASKS:
            task = self.task(task_id)
            if not task.acceptance.broader:
                continue
            _t, result = self.run_reference(task_id)
            with self.subTest(task=task_id):
                self.assertTrue(result.attempts[0].metrics.tests.broader_all_passed)


class CollateralDamageTests(HarnessTestCase):
    """A repair that also breaks something else must not read as success."""

    def test_collateral_damage_is_caught(self):
        from test_realtask_support import patch_reply

        for task_id, _filename in COLLATERAL_DAMAGE.items():
            task = self.task(task_id)
            _t, result = self.run_stages(
                task_id, [patch_reply(collateral_patch(task_id))], ["single"]
            )
            state = result.attempts[0]
            with self.subTest(task=task_id):
                self.assertOutcome(state, Outcome.REGRESSION_FAILURE)
                self.assertTrue(state.metrics.tests.targeted_all_passed)
                self.assertFalse(state.metrics.tests.broader_all_passed)


class NoCrossContaminationTests(unittest.TestCase):
    """A run must not be able to touch a fixture it was not asked about."""

    def test_each_fixture_directory_is_self_contained(self):
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            root = task.root
            with self.subTest(task=task.task_id):
                for rel in task.source.paths:
                    self.assertTrue((root / "source" / rel).is_file(), rel)
                self.assertTrue(task.source_manifest_path.is_file())
                self.assertTrue(task.tests_dir.is_dir())

    def test_no_fixture_references_another_fixture_by_path(self):
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            root = task.root
            joined = "\n".join(
                p.read_text(errors="replace") for p in root.rglob("*") if p.is_file()
            )
            for other in iter_fixture_manifests(TASKS_ROOT):
                if other.parent == root:
                    continue
                with self.subTest(task=task.task_id, other=other.parent.name):
                    self.assertNotIn(other.parent.name, joined)

    def test_fixtures_from_different_repositories_share_no_bytes(self):
        """Related fixtures may share a snapshot; unrelated ones never may.

        The five auto-ingest fixtures are deliberately five angles on one file,
        so byte-sharing inside a repository is expected. Byte-sharing across
        repositories would mean a fixture is a relabelled copy.
        """
        import hashlib

        by_digest: Dict[str, List[str]] = {}
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            digest = hashlib.sha256()
            for rel in sorted(task.source.paths):
                digest.update(rel.encode())
                digest.update((task.source_dir / rel).read_bytes())
            by_digest.setdefault(digest.hexdigest(), []).append(task.task_id)

        for shared in by_digest.values():
            if len(shared) < 2:
                continue
            repositories = {
                load_task(TASKS_ROOT / name / "task.json").source.repository
                for name in shared
            }
            with self.subTest(fixtures=sorted(shared)):
                self.assertEqual(
                    len(repositories), 1,
                    "{} share a snapshot but come from {}".format(
                        sorted(shared), sorted(repositories)
                    ),
                )


class ReferenceSolutionHygieneTests(unittest.TestCase):
    def test_every_reference_diff_is_a_valid_unified_diff(self):
        for task_id, filename in list(REFERENCE_SOLUTIONS.items()) + list(
            COLLATERAL_DAMAGE.items()
        ):
            path = REPO_ROOT / filename
            with self.subTest(task=task_id):
                self.assertTrue(path.is_file(), filename)
                text = path.read_text()
                self.assertTrue(text.startswith("diff --git "), filename)
                # A new-file diff has no `--- a/` side; it has /dev/null.
                self.assertTrue(
                    "--- /dev/null" in text or "--- a/" in text, filename
                )
                self.assertIn("+++ b/", text)
                self.assertNotIn("--- a/a/", text, "doubled strip prefix")
                self.assertNotIn("+++ b/b/", text, "doubled strip prefix")
                self.assertTrue(text.endswith("\n"), filename)

    def test_reference_diffs_are_tracked_and_committed(self):
        for filename in list(REFERENCE_SOLUTIONS.values()) + list(COLLATERAL_DAMAGE.values()):
            completed = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", filename],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(
                completed.returncode, 0,
                "{} is not tracked; a reference solution outside version control "
                "is not reviewable".format(filename),
            )


def load_grader(task_id: str):
    """Import a fixture's answer grader as a module, the way a candidate runs it."""
    import importlib.util

    path = TASKS_ROOT / task_id / "tests" / "check_answer.py"
    spec = importlib.util.spec_from_file_location("grader_" + task_id, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def keyword_dump(task_id: str) -> str:
    """An answer made *only* of the grader's own trigger vocabulary.

    Built from the grader's tables rather than hand-written, so adding a needle
    to a fixture cannot quietly make this test vacuous -- and so the attack
    stays sharp as the fixture's trigger set changes.
    """
    module = load_grader(task_id)
    words = []
    for _name, all_of, any_of in module.REQUIRED_FINDINGS:
        for needle in list(all_of) + list(any_of):
            for token in re.findall(r"[a-z_][a-z0-9_]*", needle.lower()):
                if token not in words:
                    words.append(token)
    return json.dumps({
        "root_cause": " ".join(words),
        "relevant_files": words[-1:],
        "plan": words[:3],
        "risks": words[3:6],
        "confidence": 1.0,
    })


def scattered_triggers(task_id: str) -> str:
    """One trigger per sentence: every needle present, no sentence asserting."""
    module = load_grader(task_id)
    return json.dumps({
        "root_cause": ". ".join(
            needle
            for _name, all_of, any_of in module.REQUIRED_FINDINGS
            for needle in list(all_of) + list(any_of)
        )
    })


class AnalysisGraderTests(unittest.TestCase):
    """An analysis fixture is only as good as the grader that judges it.

    These tests attack the grader directly rather than trusting it. The one that
    matters most is the keyword dump: a grader that scans the whole answer for
    trigger substrings can be satisfied by emitting the vocabulary without
    explaining anything, and then it measures nothing at all.
    """

    def grader(self, task_id: str):
        return load_grader(task_id)

    def run_grader(self, task_id: str, answer: str):
        import subprocess
        import tempfile

        module_path = TASKS_ROOT / task_id / "tests" / "check_answer.py"
        with tempfile.TemporaryDirectory() as tmp:
            answer_file = Path(tmp) / "answer.json"
            answer_file.write_text(answer, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(module_path), str(answer_file)],
                capture_output=True, text=True, timeout=120,
                env={"PATH": "/usr/bin:/bin", "HOME": tmp},
            )
        return completed.returncode, completed.stdout + completed.stderr

    def keyword_dump(self, task_id: str) -> str:
        return keyword_dump(task_id)

    def test_the_reference_answer_satisfies_its_grader(self):
        for task_id in ANALYSIS_TASKS:
            with self.subTest(task=task_id):
                code, output = self.run_grader(task_id, reference_answer(task_id))
                self.assertEqual(code, 0, output)

    def test_a_keyword_dump_is_rejected(self):
        for task_id in ANALYSIS_TASKS:
            with self.subTest(task=task_id):
                code, output = self.run_grader(
                    task_id, keyword_dump(task_id)
                )
                self.assertNotEqual(
                    code, 0,
                    "{} can be passed by echoing its own trigger words:\n{}".format(
                        task_id, output
                    ),
                )

    def test_an_empty_answer_is_rejected(self):
        for task_id in ANALYSIS_TASKS:
            with self.subTest(task=task_id):
                code, _output = self.run_grader(task_id, "{}")
                self.assertNotEqual(code, 0)

    def test_triggers_scattered_across_the_answer_are_rejected(self):
        """Substring search over the whole document is not a finding.

        One word per sentence, no sentence making a claim: every needle is
        present and yet nothing has been argued.
        """
        for task_id in ANALYSIS_TASKS:
            with self.subTest(task=task_id):
                code, output = self.run_grader(task_id, scattered_triggers(task_id))
                self.assertNotEqual(code, 0, output)

    def test_the_grader_explains_why_it_failed(self):
        """A grader that only prints FAIL is not debuggable by a candidate."""
        for task_id in ANALYSIS_TASKS:
            with self.subTest(task=task_id):
                _code, output = self.run_grader(task_id, "{}")
                self.assertIn("findings failed", output)
                self.assertIn("[FAIL]", output)
                self.assertRegex(output, r"missing:|expected one of:|no single clause")

    def test_every_analysis_fixture_has_a_reference_answer(self):
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            if task.deliverable != "analysis":
                continue
            with self.subTest(task=task.task_id):
                self.assertIn(task.task_id, REFERENCE_ANSWERS)

    def test_reference_answers_are_tracked(self):
        for filename in REFERENCE_ANSWERS.values():
            completed = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", filename],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(
                completed.returncode, 0,
                "{} is not tracked".format(filename),
            )


class AnalysisSatisfiableTests(HarnessTestCase):
    """The reference answer has to survive the whole runner, not just the grader.

    A grader that passes in isolation can still be unreachable through the
    harness -- grounding can reject the answer first, or the scout schema can
    reject its shape -- and then the fixture is a wall.
    """

    def answer_reply(self, task_id: str):
        from test_realtask_support import ScriptedResponse

        return ScriptedResponse(content=reference_answer(task_id))

    def test_every_reference_answer_reaches_success(self):
        for task_id in ANALYSIS_TASKS:
            task, result = self.run_stages(
                task_id, [self.answer_reply(task_id)], ["single"]
            )
            with self.subTest(task=task_id):
                self.assertEqual(task.deliverable, "analysis")
                self.assertOutcome(result.attempts[0], Outcome.SUCCESS)

    def test_a_keyword_dump_does_not_reach_success(self):
        """The adaptive dump from the grader's own tables, through the runner."""
        from test_realtask_support import ScriptedResponse

        for task_id in ANALYSIS_TASKS:
            _task, result = self.run_stages(
                task_id, [ScriptedResponse(content=keyword_dump(task_id))], ["single"]
            )
            with self.subTest(task=task_id):
                self.assertNotEqual(
                    result.attempts[0].metrics.outcome, Outcome.SUCCESS,
                    "{} was passed by echoing its own trigger words".format(task_id),
                )

    def test_an_empty_answer_does_not_reach_success(self):
        from test_realtask_support import ScriptedResponse

        for task_id in ANALYSIS_TASKS:
            _task, result = self.run_stages(
                task_id, [ScriptedResponse(content="{}")], ["single"]
            )
            with self.subTest(task=task_id):
                self.assertNotEqual(
                    result.attempts[0].metrics.outcome, Outcome.SUCCESS
                )


class MemorisationTests(HarnessTestCase):
    """Can the oracle tell a fix from a lookup table?

    The attack under test is the one any model eventually finds: read the
    oracle, notice it only ever mentions one input, and special-case that input.
    The patch still satisfies every check, still reports a green run, and leaves
    the defect exactly as wide as it was for everything the oracle did not name.

    This class is the reason the settings oracle probes five filenames the
    fixture never mentions. That probe was added *because* the memorisation
    patch passed 13/13 targeted and 20/20 broader before it existed.
    """

    def attack_patch(self, filename: str) -> str:
        return (REPO_ROOT / filename).read_text(encoding="utf-8")

    def test_a_memorising_patch_does_not_reach_success(self):
        from test_realtask_support import patch_reply

        for task_id, filename in OVERFIT_PATCHES.items():
            _task, result = self.run_stages(
                task_id, [patch_reply(self.attack_patch(filename))], ["single"]
            )
            with self.subTest(task=task_id):
                self.assertNotEqual(
                    result.attempts[0].metrics.outcome, Outcome.SUCCESS,
                    "{} is passed by a patch that memorises the oracle rather "
                    "than fixing anything".format(task_id),
                )

    def test_a_memorising_patch_applies_cleanly(self):
        """Otherwise the rejection above would only mean a broken diff."""
        from test_realtask_support import patch_reply

        for task_id, filename in OVERFIT_PATCHES.items():
            _task, result = self.run_stages(
                task_id, [patch_reply(self.attack_patch(filename))], ["single"]
            )
            seen = [o.value for o in result.attempts[0].metrics.outcomes_seen]
            with self.subTest(task=task_id):
                self.assertTrue(
                    result.attempts[0].metrics.patch.applied,
                    "the attack patch must be valid, or the test proves nothing",
                )
                self.assertNotIn(Outcome.INVALID_PATCH.value, seen)

    def test_a_memorising_patch_is_not_the_registered_solution(self):
        """A fixture may be attacked *and* have a reference solution.

        What must never happen is the attack being filed as the solution.
        """
        for task_id, filename in OVERFIT_PATCHES.items():
            with self.subTest(task=task_id):
                self.assertIn(task_id, REFERENCE_SOLUTIONS, task_id)
                self.assertNotEqual(
                    filename, REFERENCE_SOLUTIONS[task_id],
                    "the memorisation patch is registered as the reference "
                    "solution for {}".format(task_id),
                )
                self.assertNotEqual(
                    self.attack_patch(filename),
                    (REPO_ROOT / REFERENCE_SOLUTIONS[task_id]).read_text(
                        encoding="utf-8"
                    ),
                )

    def test_overfit_patches_are_tracked(self):
        for filename in OVERFIT_PATCHES.values():
            completed = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", filename],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, filename)

    def test_the_settings_oracle_probes_unnamed_filenames(self):
        """The concrete hole this class was written for, pinned in place."""
        body = (
            TASKS_ROOT / "auto_router_settings_latency_cache_path"
            / "tests" / "test_latency_cache_placement.py"
        ).read_text(encoding="utf-8")
        self.assertIn("GENERALITY_URLS", body)
        listed = body.split("GENERALITY_URLS = (", 1)[1].split(")", 1)[0]
        self.assertGreaterEqual(
            len([l for l in listed.splitlines() if l.strip()]), 5
        )
        # The memorised literal must not be the only filename in the fixture.
        self.assertIn('"sqlite:///data/fleet.sqlite3"', listed)

    def test_the_cursor_oracle_sweeps_awkward_page_sizes(self):
        body = (
            TASKS_ROOT / "assistx_answers_store_cursor_drops_ties"
            / "tests" / "test_cursor_completeness.py"
        ).read_text(encoding="utf-8")
        sizes = [
            l for l in body.splitlines()
            if l.strip().startswith("@pytest.mark.parametrize")
            and "limit" in l
        ]
        self.assertTrue(sizes, "the page-size sweep disappeared")
        self.assertIn("11", sizes[0])
        self.assertIn("100", sizes[0])


if __name__ == "__main__":
    unittest.main()
