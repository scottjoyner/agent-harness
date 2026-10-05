# Reviewing this branch

29 commits, ~27k lines. You do not need to read it in order, and most of it is
frozen fixture source you will never review by hand.

## Read these four, in this order

| # | File | Lines | What you are checking |
|---|---|---|---|
| 1 | `realtask/patch.py` | ~280 | Containment. Can a candidate patch escape the evaluation worktree? |
| 2 | `test_realtask_corpus.py` | ~1300 | What the corpus *claims* about itself. Every test here attacks something. |
| 3 | `realtask/compare.py` + `realtask/metrics.py` | ~710 | What the artifact deliberately does **not** claim. |
| 4 | `docs/REAL-TASK-BENCHMARK.md` §7, §11, and the "live run" sections | — | The reasoning, including the negative results. |

## Do not read

- `realtask/tasks/*/source/**` — frozen upstream source, byte-identical to a real
  commit. Verified by `source-manifest.json`; a seal check runs in CI.
- `test_realtask_reference_*.diff`, `test_realtask_overfit_*.diff` — reference
  solutions and attack patches. They are inputs to `test_realtask_corpus.py`.
- The bulk of `test_realtask_runner.py` — stage-by-stage orchestration.

## What to be suspicious of

Every defect found on this branch was **silent**. None failed loudly. So:

- **An assertion that can only report a boolean is a smell.** `assertTrue(x)` on
  a fourteen-check suite cost a bisect through a four-minute run to locate. It now
  prints the command, rc and output tail. New ones should too.
- **A list of field names copied by hand is a smell.** `_merge_review` omitted
  two fields and the evidence contradicted itself about a containment decision.
- **Anything derived from a table that can grow.** `_select_best_single` fell back
  to "worst" for unknown outcomes and nothing complained. There are now three
  guards of this shape; a fourth is probably hiding.
- **A test double that returns plausible constants.** `ScriptedAdapter` reported a
  constant `1.0s` per call, which hid a cost-accounting bug through 437 tests.

## Reproducing the claims

```bash
python3 -m unittest discover -s . -p 'test_*.py'   # 438 tests
python3 realtime_bench.py validate                 # 8 fixtures, 0 failures
for d in realtask/tasks/*/; do python3 seal_realtask_fixture.py --check "$d"; done
```

CI runs all of the above on Python 3.11 and 3.12 on every push.

## The claims, and their status

| Claim | Status |
|---|---|
| Fixtures are frozen, bound and sealed | verified in CI |
| Every fixture is satisfiable and discriminating | `test_realtask_corpus.py` |
| Acceptance cannot be satisfied by memorisation | two attack patches, tracked and rejected |
| Analysis graders cannot be satisfied by keyword stuffing | adaptive dump built from each grader's own tables |
| Acceptance does not depend on the host | Pillow stub + `UndeclaredDependencyTests` |
| No patch escapes the worktree | `test_realtask_containment.py` |
| Cost accounting is not double-counted | `OverheadAccountingTests`, verified live |
| An operator's patch cannot read as a model's result | `CandidateProvenanceTests` + rollup/comparison limits |
| A model can diagnose a real defect | **verified against a live endpoint** |
| A model can *pass* a fixture | **not demonstrated** — no local model can write a unified diff |

The last row is the one to press on. It is stated in the document rather than
buried, and the hypothesis that it is a prompt problem was tested and rejected.

## If you only remember one thing

The harness is built so that its own checks can fail. A claim here that cannot be
made to fail is not a claim. If you find one, that is the bug worth reporting.
