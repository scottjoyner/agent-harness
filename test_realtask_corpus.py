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
    "auto_router_idempotency_connection_lifetime":
        "test_realtask_reference_idempotency_lifetime.diff",
    "auto_router_task_contract_lane_mismatch": "test_realtask_reference_task_contract.diff",
    "auto_router_settings_latency_cache_path": "test_realtask_reference_settings.diff",
    "assistx_answers_store_cursor_drops_ties": "test_realtask_reference_answers_store.diff",
    # test_generation: the deliverable is a *new test file*, so its reference is
    # a new-file diff landing under the fixture's writable prefix.
    "auto_ingest_driver_lifetime_regression_test":
        "test_realtask_reference_regression_test.diff",
    "auto_router_task_contract_keyword_precedence":
        "test_realtask_reference_contract_regression_test.diff",
}

#: task_id -> the reference answer that must satisfy the grader. An analysis
#: deliverable is prose, so its reference is an answer rather than a diff --
#: without one there is no proof the grader can be satisfied at all.
REFERENCE_ANSWERS: Dict[str, str] = {
    "auto_ingest_shorts_plan_review": "test_realtask_reference_review.json",
    "auto_ingest_plan_shorts_contract": "test_realtask_reference_contract.json",
    "assistx_allocation_llm_capability_gate":
        "test_realtask_reference_allocation_review.json",
    "auto_router_contract_shim_single_source":
        "test_realtask_reference_contract_shim.json",
}

#: A repair of the campaign defect that also changes unrelated defaults. Used to
#: prove the broader tier catches collateral damage, not only the primary defect.
COLLATERAL_DAMAGE: Dict[str, str] = {
    "auto_ingest_plan_shorts_live_driver": "test_realtask_reference_regression.diff",
}

#: A repair that breaks behaviour where the fixture's *own targeted* checks can
#: see it. ``CollateralDamageTests`` below covers the campaign fixture, whose
#: collateral damage is invisible to its targeted tier and only shows up in the
#: broader one. The idempotency refactor's oracle pins behaviour directly --
#: twelve ledger cycles through one shared in-memory connection -- so its
#: collateral damage surfaces as a targeted failure instead. Different
#: enforcement path, different test, so it is tracked separately rather than
#: forced into a shape whose assertions would be false here.
COLLATERAL_DAMAGE_TARGETED: Dict[str, str] = {
    "auto_router_idempotency_connection_lifetime":
        "test_realtask_reference_always_close.diff",
}

#: A patch that satisfies the oracle by memorising the inputs the oracle names,
#: rather than by fixing the defect. Every one of these must be rejected: a
#: benchmark that cannot tell a fix from a lookup table measures recall of the
#: test file, not engineering.
OVERFIT_PATCHES: Dict[str, str] = {
    "auto_router_settings_latency_cache_path": "test_realtask_overfit_settings.diff",
    "auto_ingest_plan_shorts_live_driver": "test_realtask_overfit_live_driver.diff",
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


def collateral_patch_targeted(task_id: str) -> str:
    return (REPO_ROOT / COLLATERAL_DAMAGE_TARGETED[task_id]).read_text(encoding="utf-8")


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
                self.assertTestsPassed(state, "targeted")
                self.assertEqual(state.metrics.patch.syntax_ok, True)
                if task.acceptance.broader:
                    self.assertTestsPassed(state, "broader")

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
                self.assertTestsPassed(result.attempts[0], "broader")


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
                self.assertTestsPassed(state, "targeted")
                self.assertFalse(
                    state.metrics.tests.broader_all_passed,
                    "collateral damage went unnoticed, so this fixture's "
                    "broader tier proves nothing",
                )


class TargetedCollateralDamageTests(HarnessTestCase):
    """Collateral damage the fixture's own targeted tier is supposed to catch.

    The interesting property here is *where* it is caught. A refactor that
    tidies the in-memory close guard looks like an improvement and passes every
    structural assertion in the oracle; it is rejected because the behavioural
    checks in that same oracle fail. A fixture whose collateral damage only
    showed up in a broader tier would be trusting luck to enforce
    behaviour preservation.
    """

    def test_collateral_damage_is_caught_by_the_targeted_tier(self):
        from test_realtask_support import patch_reply

        for task_id, _filename in COLLATERAL_DAMAGE_TARGETED.items():
            task = self.task(task_id)
            _t, result = self.run_stages(
                task_id, [patch_reply(collateral_patch_targeted(task_id))], ["single"]
            )
            state = result.attempts[0]
            with self.subTest(task=task_id):
                self.assertOutcome(state, Outcome.TARGETED_TEST_FAILURE)
                self.assertFalse(
                    state.metrics.tests.targeted_all_passed,
                    "collateral damage was not caught by the targeted tier, so "
                    "this fixture does not enforce its own behaviour contract",
                )


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
        for task_id, filename in (
            list(REFERENCE_SOLUTIONS.items())
            + list(COLLATERAL_DAMAGE.items())
            + list(COLLATERAL_DAMAGE_TARGETED.items())
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
        for filename in (
            list(REFERENCE_SOLUTIONS.values())
            + list(COLLATERAL_DAMAGE.values())
            + list(COLLATERAL_DAMAGE_TARGETED.values())
        ):
            completed = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", filename],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(
                completed.returncode, 0,
                "{} is not tracked; a reference solution outside version control "
                "is not reviewable".format(filename),
            )

#: Artifacts in this repository that are *generated* from something else and
#: then tracked, so they can be reviewed in a diff. Each entry names the file and
#: a zero-argument callable that regenerates its expected contents.
#:
#: These are the corpus's silent-drift hazard. The test_generation reference
#: solution is a copy of the campaign oracle; the campaign reference diff is a
#: copy of ``REFERENCE_REPAIR``; the two analysis graders share a hardening
#: block. All three were consistent only because they were regenerated in the
#: same commit that changed their source. Nothing enforced that, and the failure
#: mode is not a clean error -- a desynced reference either fails somewhere
#: unrelated or, worse, stops discriminating without anything going red.
#:
#: Adding a derived artifact means adding a row here. The registry is the only
#: thing standing between a reviewer and a corpus that quietly stops measuring.
def _reference_repair() -> str:
    from test_realtask_support import REFERENCE_REPAIR

    return REFERENCE_REPAIR


def _regenerated_regression_test_diff() -> str:
    from test_realtask_support import AUTO_INGEST_BUG_FIX, TASKS_ROOT, new_file_patch

    body = (
        TASKS_ROOT / AUTO_INGEST_BUG_FIX / "tests" / "test_plan_driver_lifetime.py"
    ).read_text(encoding="utf-8")
    return new_file_patch("_realtask_tests/test_candidate_lifetime.py", body)


def _contract_keyword_precedence_diff() -> str:
    from test_realtask_support import (
        CONTRACT_KEYWORD_PRECEDENCE_TEST,
        new_file_patch,
    )

    return new_file_patch(
        "_realtask_tests/test_contract_keyword_precedence.py",
        CONTRACT_KEYWORD_PRECEDENCE_TEST,
    )


def _grader_hardening_block() -> str:
    """The shared hardening logic, as it appears in either grader."""

    path = (
        TASKS_ROOT / "auto_ingest_shorts_plan_review" / "tests" / "check_answer.py"
    )
    text = path.read_text(encoding="utf-8")
    start = text.index("#: A finding must be carried")
    end = text.index("\ndef main(argv) -> int:")
    return text[start:end]


DERIVED_ARTIFACTS: List = [
    (
        "test_realtask_reference_live_driver.diff",
        _reference_repair,
        "the inline REFERENCE_REPAIR in test_realtask_support.py",
    ),
    (
        "test_realtask_reference_regression_test.diff",
        _regenerated_regression_test_diff,
        "the campaign oracle test_plan_driver_lifetime.py",
    ),
    (
        "test_realtask_reference_contract_regression_test.diff",
        _contract_keyword_precedence_diff,
        "CONTRACT_KEYWORD_PRECEDENCE_TEST in test_realtask_support.py",
    ),
]

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


class LeaveOneOutFindingTests(HarnessTestCase):
    """Every required finding must depend on the sentence that asserts it.

    This is the strongest discrimination claim the analysis fixtures can make
    without a model in the loop, and it exists because no model has ever passed
    one. Until a capable model runs, "the grader rejects a keyword dump" is a
    weak claim: it is satisfied by a grader that only counts words. What
    matters is whether the grader can tell a complete answer from one that is
    missing exactly one substantive point.

    So for each required finding, the sentences carrying its vocabulary are
    deleted from an otherwise-perfect reference answer, and the grader must then
    fail. A finding that still passes has leaked: it is decoration, and a model
    could satisfy it without understanding anything.

    Measured across all four analysis fixtures: 22 required findings, 0 leaks.

    What this does *not* establish, recorded because I checked by trying to
    break it: it tests dependence, not specificity. Broadening a finding's
    vocabulary -- accepting an extra, common word -- is invisible here, because
    deleting the sentences carrying a *wider* vocabulary still breaks the
    grader. So this cannot tell you a finding is hard to satisfy; it tells you
    the finding is not decoration. The other direction is covered by
    :class:`MemorisationTests`, which feeds keyword dumps and scattered
    vocabulary to the same graders.
    """

    def _grader(self, task_id):
        return load_grader(task_id)

    def _reference_passes(self, task_id: str, path: Path) -> None:
        path.write_text(reference_answer(task_id), encoding="utf-8")
        self.assertEqual(self._judge_quietly(task_id, path), 0)

    def _judge_quietly(self, task_id: str, path: Path) -> int:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            return self._grader(task_id).main(["check_answer.py", str(path)])

    def test_removing_a_finding_evidence_fails_the_grader(self):
        import re

        tmp = self.tmp / "answer.json"
        checked = 0
        for task_id in ANALYSIS_TASKS:
            with self.subTest(task=task_id):
                self._reference_passes(task_id, tmp)
                reference = reference_answer(task_id)
                findings = getattr(self._grader(task_id), "REQUIRED_FINDINGS", ())
                self.assertTrue(findings, "{} declares no findings".format(task_id))
                for name, all_of, any_of in findings:
                    checked += 1
                    needles = [n for n in list(all_of) + list(any_of) if n]
                    self.assertTrue(needles, "finding has no vocabulary: {}".format(name))
                    sentences = re.split(r"(?<=[.!?])\s+", reference)
                    kept = [
                        s for s in sentences
                        if not any(n.lower() in s.lower() for n in needles)
                    ]
                    self.assertLess(
                        len(kept), len(sentences),
                        "{}: nothing in the reference carries {!r}, so this "
                        "finding cannot be shown to matter".format(task_id, name),
                    )
                    tmp.write_text(" ".join(kept), encoding="utf-8")
                    with self.subTest(finding=name):
                        self.assertNotEqual(
                            self._judge_quietly(task_id, tmp), 0,
                            "{}: {!r} still passes with its evidence removed".format(
                                task_id, name
                            ),
                        )
        self.assertGreater(checked, 0)

    def test_a_wholly_different_answer_also_fails(self):
        """Belt and braces: the same property at the extreme."""
        tmp = self.tmp / "answer.json"
        tmp.write_text(
            '{"root_cause": "I am not sure what this code does.", '
            '"relevant_files": [], "plan": [], "risks": [], "confidence": 0.1}',
            encoding="utf-8",
        )
        for task_id in ANALYSIS_TASKS:
            with self.subTest(task=task_id):
                self.assertNotEqual(self._judge_quietly(task_id, tmp), 0)


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


class DerivedArtifactTests(unittest.TestCase):
    """Generated-but-tracked files must still match what they were generated from.

    Every artifact here is committed so a reviewer can read it in a diff, and
    regenerated so it cannot rot. A desync does not announce itself: the
    test_generation reference silently stops discriminating, or the analysis
    graders drift apart and one of them quietly becomes passable again.
    """

    def test_derived_artifacts_match_their_source(self):
        for name, regenerate, source in DERIVED_ARTIFACTS:
            path = REPO_ROOT / name
            with self.subTest(artifact=name):
                self.assertTrue(path.is_file(), name)
                self.assertEqual(
                    path.read_text(encoding="utf-8"),
                    regenerate(),
                    "{} is out of date with {}; regenerate it before "
                    "committing".format(name, source),
                )

    def test_both_graders_carry_identical_hardening(self):
        """A fix to one grader's hardening must land in the other.

        The graders legitimately differ in docstring, findings and forbidden
        list; the hardening block must not. A grader that lost it would be
        passable by keyword stuffing again.
        """
        expected = _grader_hardening_block()
        self.assertIn("MAX_CLAUSE_KEYWORD_DENSITY", expected)
        self.assertIn("MIN_SUBSTANTIVE_TOKENS", expected)
        for task_id in ANALYSIS_TASKS:
            path = TASKS_ROOT / task_id / "tests" / "check_answer.py"
            with self.subTest(task=task_id):
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    expected, text,
                    "{} does not carry the shared hardening block".format(task_id),
                )
                start = text.index("#: A finding must be carried")
                stop = text.index("\ndef main(argv) -> int:")
                self.assertEqual(
                    text[start:stop], expected,
                    "{} has drifted from the shared hardening block".format(task_id),
                )

    def test_the_reference_solutions_are_all_derived_or_handwritten(self):
        """Every tracked reference must be reachable, not orphaned.

        An artifact nothing regenerates is a deliberate one; an artifact that
        claims to be generated but is not registered is how drift starts.
        """
        registered = {name for name, _f, _s in DERIVED_ARTIFACTS}
        on_disk = {
            p.name for p in REPO_ROOT.glob("test_realtask_reference_*.diff")
        }
        self.assertLessEqual(
            on_disk - registered,
            {"test_realtask_reference_refactor.diff",
             "test_realtask_reference_idempotency_lifetime.diff",
             "test_realtask_reference_regression.diff",
             "test_realtask_reference_always_close.diff",
             "test_realtask_reference_settings.diff",
             "test_realtask_reference_task_contract.diff",
             "test_realtask_reference_answers_store.diff"},
            "a reference diff appeared that no test can regenerate",
        )


class UndeclaredDependencyTests(unittest.TestCase):
    """No acceptance tier may depend on a package the harness does not declare.

    Found the hard way: CI reported 24 failures that reproduced nowhere, all of
    them ``REGRESSION_FAILURE`` on the campaign fixture, because
    ``cli._brand_check`` opens with ``from PIL import Image`` and Pillow happened
    to be installed on the machine that wrote the fixtures. The same patch, the
    same fixture, two different verdicts depending on the host.

    That makes the acceptance signal a property of the machine rather than of the
    patch, which is the one thing this harness exists to avoid. The convention
    is already established -- the auto-router fixtures stub ``pydantic_settings``
    -- so this test enforces it rather than leaving it to memory.
    """

    #: Modules the harness itself provides, so a frozen source may use them.
    HARNESS_PROVIDED = frozenset({"pytest"})

    def non_stdlib_imports(self, source: Path) -> set:
        import ast
        import sys as _sys

        stdlib = set(getattr(_sys, "stdlib_module_names", ()))
        found = set()
        tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in stdlib:
                        found.add(root)
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import, always in-tree
                    continue
                root = (node.module or "").split(".")[0]
                if root and root not in stdlib:
                    found.add(root)
        return found - self.HARNESS_PROVIDED

    def stubbed_modules(self, tests_dir: Path) -> set:
        """Third-party modules an oracle stubs, via sys.modules or _stub()."""
        import re

        stubbed = set()
        for path in tests_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(
                r"""(?:_stub|sys\.modules\[)\s*["']([A-Za-z_][\w.]*)["']""", text
            ):
                stubbed.add(match.group(1).split(".")[0])
            for match in re.finditer(r"""["']([A-Za-z_][\w.]*)["']\s*""", text):
                stubbed.add(match.group(1).split(".")[0])
        return stubbed

    def test_every_third_party_import_in_a_snapshot_is_accounted_for(self):
        """Every non-stdlib import in a frozen snapshot is stubbed or justified.

        Not merely "stubbed": the fixtures sharing the campaign snapshot only
        drive the plan path, so they never reach ``_brand_check`` or the Neo4j
        import and have no need to stub them. What must not happen is a
        dependency being *unmentioned* -- so the ones deliberately left alone are
        listed with a reason, and anything new fails here.
        """
        offenders = []
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            stubbed = self.stubbed_modules(task.tests_dir)
            justified = UNREACHABLE_DEPENDENCIES.get(task.task_id, {})
            for rel in task.source.paths:
                for module in sorted(self.non_stdlib_imports(task.source_dir / rel)):
                    if module in stubbed or module in justified:
                        continue
                    offenders.append(
                        "{}: {} imports {!r}; no oracle stubs it and no reason "
                        "is recorded".format(task.task_id, rel, module)
                    )
        self.assertEqual(
            offenders, [],
            "an acceptance tier that imports an undeclared third-party package "
            "changes verdict with the host:\n  " + "\n  ".join(offenders),
        )

    def test_every_justification_is_specific(self):
        """A bare module name is not a reason."""
        for task_id, modules in UNREACHABLE_DEPENDENCIES.items():
            loaded = load_task(TASKS_ROOT / task_id / "task.json")
            self.assertEqual(loaded.task_id, task_id)
            for module, reason in modules.items():
                with self.subTest(task=task_id, module=module):
                    self.assertGreater(
                        len(reason), 40,
                        "record why {!r} is safe for {}".format(module, task_id),
                    )

    def test_oracles_import_only_stdlib_pytest_and_siblings(self):
        """The oracles must not need installing anything either.

        Sibling modules that live in the fixture's own ``tests/`` directory are
        part of the fixture, not third-party packages.
        """
        offenders = []
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            siblings = {path.stem for path in task.tests_dir.glob("*.py")}
            # The frozen package under test, which the oracle must import.
            under_test = {rel.split("/")[0] for rel in task.source.paths}
            for path in sorted(task.tests_dir.glob("*.py")):
                for module in sorted(self.non_stdlib_imports(path)):
                    if module in siblings or module in under_test:
                        continue
                    if module == "pytest":
                        continue
                    offenders.append(
                        "{}: {} imports {!r}".format(task.task_id, path.name, module)
                    )
        self.assertEqual(offenders, [])

    def _pil_blocker(self, directory: Path) -> Path:
        blocker = directory / "sitecustomize.py"
        blocker.write_text(
            "import sys\n"
            "class _Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] == 'PIL':\n"
            "            raise ModuleNotFoundError(name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, _Block())\n",
            encoding="utf-8",
        )
        return blocker

    def test_the_pil_blocker_actually_blocks(self):
        """Otherwise the test below would pass because blocking does nothing."""
        import os
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self._pil_blocker(Path(tmp))
            env = dict(os.environ)
            env["PYTHONPATH"] = tmp
            blocked = subprocess.run(
                [sys.executable, "-c", "import PIL"], capture_output=True,
                text=True, timeout=60, env=env,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("ModuleNotFoundError", blocked.stderr)
            unblocked = subprocess.run(
                [sys.executable, "-c", "import PIL"], capture_output=True,
                text=True, timeout=60,
            )
            if unblocked.returncode != 0:
                self.skipTest("Pillow is not installed here anyway")

    def test_the_campaign_broader_tier_passes_without_pillow(self):
        """The CI failure, encoded as a test.

        ``cli._brand_check`` opens with ``from PIL import Image`` before it looks
        at anything, so on a host without Pillow the brand check raised
        ModuleNotFoundError and the campaign fixture reported
        REGRESSION_FAILURE for a patch that was in fact correct -- 24 tests red in
        CI, none of them reproducible locally. The oracle now stubs PIL; this
        asserts the tier still passes with the real package made unimportable,
        which is the condition CI runs under.
        """
        import os
        import subprocess
        import tempfile

        task = load_task(
            TASKS_ROOT / "auto_ingest_plan_shorts_live_driver" / "task.json"
        )
        with tempfile.TemporaryDirectory() as tmp:
            self._pil_blocker(Path(tmp))
            work = Path(tmp) / "work"
            (work / "_realtask_tests").mkdir(parents=True)
            for rel in task.source.paths:
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((task.source_dir / rel).read_bytes())
            for package in ("auto_ingest", "auto_ingest/shorts"):
                init = work / package / "__init__.py"
                init.parent.mkdir(parents=True, exist_ok=True)
                init.touch()
            for path in task.tests_dir.glob("*.py"):
                (work / "_realtask_tests" / path.name).write_text(
                    path.read_text(encoding="utf-8"), encoding="utf-8"
                )

            env = dict(os.environ)
            env["PYTHONPATH"] = tmp
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 str(work / "_realtask_tests" / "test_shorts_cli_surface.py")],
                capture_output=True, text=True, timeout=300,
                cwd=str(work), env=env,
            )
            self.assertEqual(
                completed.returncode, 0,
                "the campaign broader tier needs Pillow installed, so acceptance "
                "depends on the host rather than the patch:\n{}".format(
                    completed.stdout[-3000:]
                ),
            )
            self.assertIn("14 passed", completed.stdout)


#: Third-party modules a frozen snapshot imports that a fixture's oracles
#: deliberately do not stub, with the reason that is safe for that fixture.
#:
#: The five auto-ingest fixtures share one snapshot, but only the campaign
#: fixture drives the whole CLI surface. The others reach ``_cmd_plan`` and
#: nothing else, so they never execute ``_brand_check`` (Pillow) or the driver
#: factory (neo4j).
UNREACHABLE_DEPENDENCIES: Dict[str, Dict[str, str]] = {
    "auto_router_contract_shim_single_source": {
        "pydantic": "an analysis deliverable judges the answer text; the snapshot is "
                    "never imported, so its third-party imports are unreachable",
        "assistx": "the canonical package this module tries to import; absent from "
                   "the snapshot on purpose, since that fallback is the subject",
    },
    "auto_ingest_shorts_driver_helper": {
        "PIL": "only _brand_check imports Pillow, and no oracle calls it",
        "neo4j": "the driver factory is replaced by the oracle's own stub",
        "auto_ingest_config": "reaches the CLI import line, which the oracle "
                              "satisfies by stubbing the name itself",
    },
    "auto_ingest_plan_shorts_contract": {
        "PIL": "only _brand_check imports Pillow, and no oracle calls it",
        "neo4j": "the driver factory is replaced by the oracle's own stub",
        "auto_ingest_config": "reaches the CLI import line, which the oracle "
                              "satisfies by stubbing the name itself",
    },
    "auto_ingest_driver_lifetime_regression_test": {
        "PIL": "only _brand_check imports Pillow, and no oracle calls it",
        "neo4j": "the driver factory is replaced by the oracle's own stub",
        "auto_ingest_config": "reaches the CLI import line, which the oracle "
                              "satisfies by stubbing the name itself",
    },
    "auto_ingest_plan_shorts_live_driver": {
        "neo4j": "the driver factory is replaced by the oracle's own stub",
    },
    "auto_ingest_shorts_plan_review": {
        "PIL": "an analysis fixture never executes the CLI; its grader reads "
               "the captured answer text and nothing else",
        "neo4j": "an analysis fixture never executes the CLI, so no driver is "
                 "ever constructed",
        "auto_ingest_config": "an analysis fixture never imports the CLI at all",
    },
}


class DocumentationCoverageTests(unittest.TestCase):
    """Documentation drift is a defect, and it is silent.

    Twice on this branch a write raised *after* the code was committed, so the
    commit looked complete while the changelog was missing an entry for a bug
    just fixed. Nothing was red. A reader of the record would have believed a
    fix was undocumented -- or, worse, that no fix had happened.

    These tests make the three tables a reader actually consults fail loudly
    instead: the failure taxonomy, the shipped-fixture list, and the test-module
    index. They are derived from the code, so a new outcome or fixture cannot be
    added without a reader being able to find it.
    """

    DOC = "docs/REAL-TASK-BENCHMARK.md"

    def doc_text(self) -> str:
        return (REPO_ROOT / self.DOC).read_text(encoding="utf-8")

    def test_every_outcome_appears_in_the_failure_taxonomy_table(self):
        """The table an operator reads to interpret a result must be complete."""
        rows = set(
            re.findall(r"^\|\s*`([A-Z_]+)`\s*\|", self.doc_text(), re.MULTILINE)
        )
        missing = sorted(o.value for o in Outcome if o.value not in rows)
        self.assertEqual(
            missing, [],
            "{} defines outcomes the documented taxonomy does not explain: "
            "{}. An operator reading the table cannot interpret a run that "
            "produced one.".format(self.DOC, missing),
        )

    def test_every_outcome_has_a_taxonomy_explanation_in_code(self):
        from realtask.taxonomy import _EXPLANATIONS

        missing = sorted(
            o.value for o in Outcome
            if not _EXPLANATIONS.get(o, "").strip()
        )
        self.assertEqual(missing, [])

    def test_the_taxonomy_table_does_not_invent_outcomes(self):
        """The other direction: a row for an outcome that no longer exists."""
        rows = set(
            re.findall(r"^\|\s*`([A-Z_]+)`\s*\|", self.doc_text(), re.MULTILINE)
        )
        known = {o.value for o in Outcome}
        invented = sorted(r for r in rows if r.endswith(("_FAILURE", "_MISMATCH", "OUTPUT")) and r not in known)
        self.assertEqual(invented, [], "documented outcomes that do not exist")

    def test_every_shipped_fixture_is_listed(self):
        text = self.doc_text()
        missing = sorted(
            root.parent.name
            for root in TASKS_ROOT.glob("*/task.json")
            if "`{}`".format(root.parent.name) not in text
        )
        self.assertEqual(
            missing, [],
            "fixtures on disk that the shipped-fixtures table does not "
            "list: {}".format(missing),
        )

    def test_every_test_module_is_listed(self):
        text = self.doc_text()
        missing = sorted(
            path.stem
            for path in REPO_ROOT.glob("test_*.py")
            if "`{}.py`".format(path.stem) not in text
        )
        self.assertEqual(
            missing, [],
            "test modules that the test index does not list: {}".format(missing),
        )

    def test_the_docs_state_exactly_what_has_and_has_not_been_verified(self):
        """The document must not overclaim, and must not underclaim either.

        Two failure modes, both seen on this branch. Claiming less than is true
        hides that a live run happened and what it found. Claiming more hides
        that no model has yet solved a fixture, which is the fact a reader needs
        most when weighing a single non-SUCCESS run from a small model.

        So both halves are asserted: the harness has been run against a live
        endpoint, and it has never produced a SUCCESS.
        """
        # Markdown hard-wraps, so a phrase can straddle a newline. Collapse
        # whitespace before matching or this test fails on reflow alone.
        text = " ".join(self.doc_text().lower().split())

        # Assert on a boolean, not on the substring: assertIn would dump the
        # whole document into the failure message, which is 900 lines of noise
        # around a one-line problem.
        self.assertTrue(
            "run against a real openai-compatible server" in text,
            "the document no longer records that a live endpoint was used; if "
            "that is no longer true, say so here rather than deleting it",
        )
        self.assertTrue(
            "ever produced a `success`" in text,
            "the document must keep stating that no model has yet solved a "
            "fixture; without it a reader may take one small-model failure as "
            "evidence about the benchmark rather than about the model",
        )

    def test_the_docs_do_not_stale_counts(self):
        """A count in prose is a claim that decays quietly."""
        from realtask.taxonomy import Outcome

        count = len(list(Outcome))
        # "the eleven outcomes" and friends.
        flat = " ".join(self.doc_text().lower().split())
        for word in ("eleven", "twelve"):
            self.assertNotIn(
                "the {} outcomes".format(word), flat,
                "the layout section names a fixed number of outcomes; there are "
                "{} now".format(count),
            )

    def test_the_changelog_documents_the_current_taxonomy(self):
        """Whatever the newest entry is, it must not predate a live finding."""
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("TOOL_CALL_REQUESTED", changelog)
        self.assertIn("billed to the harness a second time", changelog)
if __name__ == "__main__":
    unittest.main()


class DeliverableCoverageDisclosureTests(unittest.TestCase):
    """The corpus must not imply a breadth of coverage it does not have.

    The shipped-fixtures table says "five families", and that is true. It is also
    misleading on its own: four of those five families, and *both* fixtures whose
    deliverable is ``analysis``, come from one repository and one defect. A model
    that writes patches well and reviews badly cannot be distinguished by this
    corpus, and the hardened analysis graders have only ever faced one defect
    class.

    Rather than assert a breadth the corpus does not yet have -- which would mean
    either shipping a fixture on a defect that may not be one, or landing a skipped
    test, both worse than the gap -- these tests pin the *disclosure*. If a future
    change widens or narrows the coverage, the documented figures must move with it,
    so the gap cannot quietly stop being true while the table still implies it is.
    """

    def coverage(self):
        """Repository spread by deliverable and family, as short names.

        Short names because that is how the document writes them; comparing
        against ``scottjoyner/auto-ingest`` would fail against a correct doc.
        """
        by_deliverable: Dict[str, set] = {}
        by_family: Dict[str, set] = {}
        per_repo: Dict[str, int] = {}
        for manifest in iter_fixture_manifests(TASKS_ROOT):
            task = load_task(manifest)
            repo = task.source.repository.split("/")[-1]
            by_deliverable.setdefault(task.deliverable, set()).add(repo)
            by_family.setdefault(task.task_family, set()).add(repo)
            per_repo[repo] = per_repo.get(repo, 0) + 1
        campaign = max(per_repo, key=lambda r: per_repo[r])
        outside = sorted(f for f, repos in by_family.items() if repos - {campaign})
        return by_deliverable, by_family, campaign, outside

    def doc_text(self) -> str:
        return (REPO_ROOT / "docs/REAL-TASK-BENCHMARK.md").read_text(encoding="utf-8")

    def doc_flat(self) -> str:
        """Whitespace-collapsed, because Markdown hard-wraps.

        And assertions run against this rather than ``assertIn`` on the raw text:
        a failure would otherwise dump 900 lines around a one-line problem.
        """
        return " ".join(self.doc_text().split())

    def test_the_document_states_the_actual_deliverable_spread(self):
        by_deliverable, _by_family, _campaign, _outside = self.coverage()
        for deliverable, repos in by_deliverable.items():
            with self.subTest(deliverable=deliverable):
                self.assertTrue(
                    "| `{}` | {} |".format(
                        deliverable,
                        "auto-ingest only" if repos == {"auto-ingest"}
                        else ", ".join(sorted(repos)),
                    ) in self.doc_flat(),
                    "docs/REAL-TASK-BENCHMARK.md does not state the real "
                    "repository spread for the {!r} deliverable".format(deliverable),
                )

    def corpus_shape_row(self):
        """The corpus row of the corpus-shape table, as a list of cells.

        Parsed from the table rather than matched as a literal row: matching a
        whole row meant hardcoding the source-identity count too, so adding a
        fixture failed for the wrong reason.
        """
        match = re.search(
            r"^\|\s*corpus\s*\|(.+?)\|\s*$", self.doc_text(), re.MULTILINE
        )
        self.assertIsNotNone(match, "the corpus-shape table has no corpus row")
        return [cell.strip() for cell in match.group(1).split("|")]

    def test_the_document_states_the_actual_family_count_outside_the_campaign(self):
        """Only the derived column is asserted: families outside the campaign.

        That is the figure that quietly lies, because it is the one a reader
        cannot check by counting fixtures.
        """
        _by_deliverable, _by_family, _campaign, outside = self.coverage()
        cells = self.corpus_shape_row()
        self.assertEqual(
            cells[-1], str(len(outside)),
            "the corpus-shape row must end with the true count of families "
            "outside the campaign repository (currently {})".format(outside),
        )

    def test_the_corpus_shape_row_lists_every_family_the_corpus_contains(self):
        """A count of 5 is meaningless if the corpus holds a sixth."""
        _bd, by_family, _campaign, _outside = self.coverage()
        cells = self.corpus_shape_row()
        self.assertEqual(
            int(cells[2]), len(by_family),
            "the corpus-shape row claims {} families; the corpus has {}".format(
                cells[2], sorted(by_family)
            ),
        )

    def test_the_analysis_gap_is_named_rather_than_implied(self):
        """While `analysis` is single-repository, the document must say so."""
        by_deliverable, _by_family, _campaign, _outside = self.coverage()
        analysis = by_deliverable.get("analysis", set())
        if len(analysis) <= 1:
            for phrase in ("confined to the campaign repository",):
                self.assertTrue(
                    phrase in self.doc_flat(),
                    "the analysis coverage gap must be stated in the document, "
                    "not left for a reader to infer from a table of families",
                )

    def test_the_campaign_repository_is_still_identified_as_the_campaign(self):
        _bd, _bf, campaign, outside = self.coverage()
        campaign_fixture = load_task(
            TASKS_ROOT / "auto_ingest_plan_shorts_live_driver" / "task.json"
        )
        self.assertEqual(
            campaign_fixture.source.repository.split("/")[-1],
            campaign,
            "the campaign repository must still be the one supplying the fixture "
            "this document calls the campaign",
        )
        self.assertTrue(
            outside,
            "no family exists outside the campaign repository, so the "
            "corpus-shape table has no meaningful last column",
        )


if __name__ == "__main__":
    unittest.main()
