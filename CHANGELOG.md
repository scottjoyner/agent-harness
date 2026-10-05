# Agent Harness Changelog

## Unreleased

### Added

- **A live 30B run, which found a defect in the analysis graders.** No download was
  needed: 32 models were already on disk, and reading GGUF headers directly
  (`general.architecture`) picked one the running LM Studio build could load --
  `meta/muse-glimmer`, 30B, llama arch, Q4_K_M -- after a ternary quant and an
  unsupported architecture both failed to load.

  On `auto_router_contract_shim_single_source` its scout reached `outcome=SUCCESS`
  with `grounding.expected_overlap` of 1.0, and its `root_cause` independently found
  all three findings the fixture encodes. **The 0.8B/3B protocol wall was model
  capability, not a harness defect.**

  A full `--stage single` attempt was then graded `TARGETED_TEST_FAILURE` at 3 of 6
  findings -- and two of those three are grader false negatives. The model wrote
  "falling back to local mirrors ... a divergent local implementation" where the
  grader demanded the literal token `importerror`, and "cause late runtime failures
  ... not observable at import time" where it accepted only `at use` / `later` /
  `downstream` / `deferred`. Hardening the graders against keyword stuffing made them
  lexically brittle, so they now reject correct paraphrases.

  **Fixed**, by widening consequence vocabulary while keeping construct anchors
  strict: a paraphrase must still name `TraceEvent` or `_USING_CANONICAL`, but may
  describe the consequence in its own words. The model's answer now scores 5 of 6
  instead of 3, and the sixth is a genuine miss that was left failing. Precision was
  verified rather than assumed -- a bare list of the newly added phrases is still
  rejected, and leave-one-out still fails on both the reference answer and the real
  one. The model's answer is tracked verbatim with its exact score pinned, so
  re-narrowing the vocabulary fails a test instead of quietly costing two findings.

- **`auto_router_idempotency_connection_lifetime`** — a `small_refactor` fixture, and
  the last family that was still measured on a single codebase.
  `auto_router/request_idempotency.py` owns a SQLite connection's lifetime in four
  places (`_init_db`, `reserve`, `get`, `transition`), each spelling out the same
  acquire / commit-or-rollback / close shape, three of them opening their own
  `BEGIN IMMEDIATE`.

  The point of the fixture is that one of those four copies is load-bearing.
  `_connect` returns a single shared connection for `:memory:`, so
  `if not self.in_memory` is the only reason an in-memory ledger survives past its
  first operation. A refactor that tidies it into a plain `finally: conn.close()`
  satisfies every structural assertion and then raises
  `sqlite3.ProgrammingError` on every later call. The oracle drives twelve
  reserve/get/transition cycles through one in-memory ledger so that version is
  rejected rather than rewarded.

  A second trap is pinned: `_database_path` keeps the leading slash, so
  `sqlite:///ledger.sqlite3` resolves to the absolute path `/ledger.sqlite3`. That
  looks wrong, and a refactor gathering connection setup would be tempted to
  normalise it, silently changing where the ledger is written. The oracle pins the
  current parsing, quirks included.

  The structural check is a regex, not a substring count: `__init__` also branches
  on `in_memory` to create the parent directory, and a bare count would have
  forbidden that legitimate second use. My first version had exactly that bug.

- **`TargetedCollateralDamageTests`** — a second collateral-damage tier, tracking
  `COLLATERAL_DAMAGE_TARGETED`. The existing `CollateralDamageTests` assumes the
  targeted tier still passes and only the broader tier notices; that is true for the
  campaign fixture and false here, where the oracle pins behaviour directly and the
  damage surfaces as a targeted failure. Forcing it into the existing shape would
  have made that test assert something untrue. Worth stating plainly: a fixture
  whose collateral damage only showed up in a broader tier would be trusting luck to
  enforce behaviour preservation. This one does not.

- **`auto_router_task_contract_keyword_precedence`** — a `test_generation` fixture,
  and the first time a second codebase exercises the meta-oracle that executes
  candidate-authored code. `normalize_task_kind` classifies a task by scanning a
  fixed keyword map and returning on the *first* substring match; `analysis` is
  listed before `code` and matches the bare word `review`. So "review the failing
  handler and add a regression test" is classified `analysis`, which flips
  `task_evidence_required` to true and hands the task the analysis plan. The same
  request without the word "review" is classified `code`.

  Verified by execution before the fixture was written, not by reading. Six attacks
  on the meta-oracle are rejected: an always-failing test, an always-passing test, a
  test that passes for an unrelated reason, a test pinned to the *buggy* behaviour
  (which fails both checks), a test with an unreachable import, and no test at all.

  One trap worth recording. The reference test initially imported
  `auto_router.task_contract` without the `.py` suffix, and the grounding gate
  rejected the whole attempt as `GROUNDING_FAILURE` — a correct test, refused for
  naming its module in Python import form rather than path form. The reference now
  names the file explicitly.

  Deliberately shares a source identity with `auto_router_task_contract_lane_mismatch`:
  different defect, different capability. `test_a_campaign_is_diverse_in_family_and_defect`
  enforces that a shared snapshot still means two distinct tasks.

- **`auto_router_contract_shim_single_source`** — a `contract_reasoning` fixture on
  `auto-router`. The module documents that it re-exports the canonical
  `assistx.contracts` types "so every repo emits the single source-of-truth
  envelope", and keeps that contract on only one of its two paths. In the
  `except ImportError` fallback, `TraceEvent` and `TraceGroup` become classes whose
  bodies are `pass` — importable and instantiable, but carrying no data, so the loss
  surfaces when something reads an attribute rather than at import. `SCHEMA_VERSION`
  becomes a hardcoded literal that can drift with nothing comparing it. And
  `_USING_CANONICAL`, the flag recording which path was taken, is exported and read
  by nothing in the repository.

  Verified against the frozen source before the fixture was written. Chosen as a
  different defect class again: a documented contract kept selectively, rather than
  an unenforceable admission gate or a lifecycle ordering.

- **`assistx_allocation_llm_capability_gate`** — a `code_review` fixture on
  `auto-assist`, closing the disclosed gap that every `analysis` fixture came from
  the campaign repository. `allocation_engine.py` ranks task/node/model placements
  and admits a node when `required.issubset(capabilities | {"llm"})` — the union
  grants `llm` before the subset test, so any node satisfies an `llm`
  requirement. The rejection path applies the same exemption
  (`required - capabilities - {"llm"}`), so the `rejected` list stays empty as
  well. Confirmed against the frozen source: a node advertising no capabilities at
  all is recommended for an `llm` task, with evidence identical to a node that
  genuinely advertises it.

  Chosen because it is a different defect class from the other eight — an
  unenforceable admission contract rather than a lifecycle ordering, a path
  derivation, or a keyset cursor — which is the point of adding it. Its grader
  carries the identical hardening block as the other two, and
  `DerivedArtifactTests` now checks that against every analysis fixture instead of
  a hardcoded pair, so a fourth grader cannot join without joining the check.

### Fixed

- **The corpus-shape disclosure guards could not tell a new fixture from a stale
  table.** They asserted a literal table row, which hardcoded the source-identity
  count, so adding a fixture failed for the wrong reason. They now parse the row and
  assert only the derived column — families outside the campaign repository — plus
  that the stated family count matches the fixtures on disk. Verified by
  overstating and understating each figure.

- **Every single-vs-swarm comparison this harness produced was structurally n=1.**
  `--single-attempts` existed; the swarm side could not be repeated at all. So the
  artifact invited a comparison — is the difference the strategy, or the run? — that
  was unanswerable by construction, and said nothing about sample size. `--swarm-attempts`
  now exists, both sides report `sample_sizes`, a single observation is named as
  one, the swarm side is described as one sample even after replication (role
  separation is nondeterministic, so a per-attempt delta stays confounded with
  run-to-run variation), and the roll-up records `attempts_per_task` and says when
  every pooled task contributed a single attempt.

  The honest limit is stated rather than implied away: replication makes variance
  observable, it does not make one task generalisable.

- **The corpus-shape table implied a breadth of task-type coverage the corpus does
  not have.** It reported "families 5" against a 3-repository corpus, which reads as
  broader than it is: `bug_fix` spans all three repositories, but `code_review`,
  `contract_reasoning`, `small_refactor` and `test_generation` all come from the
  campaign repository, and **both** `analysis`-deliverable fixtures are
  auto-ingest only. So a model that writes patches well and reviews badly cannot
  be distinguished by this corpus, and the hardened analysis graders have only
  ever faced one defect class.

  The table now carries a "families outside the campaign repo" column and the real
  deliverable-to-repository spread, with the gap stated in prose rather than left
  for a reader to infer from a count. `DeliverableCoverageDisclosureTests` derives
  the actual spread from the fixtures and fails if the documented figures drift,
  so the gap cannot quietly stop being true while the table still implies it is.

  Not asserted: that `analysis` spans two repositories. That would require either
  shipping a fixture built on a defect that may not be one, or landing a skipped
  test — both worse than a disclosed gap. The honest position is that closing it
  needs an `analysis` fixture from `auto-router` or `auto-assist`, found by reading
  frozen source the way the other three were.

- **An operator-supplied patch was indistinguishable from a model's result.**
  `--scout-file` recorded provenance; `--patch-file` recorded nothing. Attempts are
  now stamped `candidate_source` (`model` or `operator_patch_file`) and it is
  reported in `metrics.json`, `comparison.json` and the roll-up, with a scope limit
  whenever an operator-supplied candidate is involved. The manifest records the
  operator's path, SHA-256 and length.

  Found by running the harness for real: a live `--stage review` run with a
  hand-written reference patch scored targeted 1/1 and broader 1/1, and nothing in
  the evidence said a human had done it.

  The field lives on `AttemptMetrics`, not `PatchMetric`, because `PatchMetric`
  fields are copied by hand in `_merge_review` — where `safety_ok` and
  `safety_reason` once went missing. This is the third hand-maintained field list
  in the harness to drop a field; the roll-up's `_ATTEMPT_FIELDS` was the one that
  surfaced it.

- **Artifacts predating provenance are bucketed `unknown`, not assumed to be model
  attempts.** The first version of the roll-up crashed sorting `None`, and the
  obvious repair — defaulting to `model` — would have been the wrong guess in
  exactly the direction that flatters a result.

### Verified

- **The last unexercised live link is a model writing a patch, and the obvious
  explanation for it is wrong.** The hypothesis was an under-specified interface:
  the implementer contract asks for `"a complete unified diff"` without showing
  one. Tested by adding a generic worked example of the diff skeleton, unrelated
  to any fixture. It changed the output shape — the model began emitting `--- a/`,
  `+++ b/`, `@@` — and still reproduced the whole file rather than a minimal diff,
  so the response never closed. Reverted. The limit is model capability, not
  interface clarity; the contract already names the format exactly.

- **Acceptance executed against real evidence for the first time**, via the review
  stage with an operator-supplied patch: `safety_ok` true, one bound file changed,
  targeted 1/1 and broader 1/1. Reaching it required the work-root fix below.

- **A real model diagnosed a real defect through the harness.** Eight live runs
  against LM Studio (loopback), two fixtures, two models, `single` and `swarm`.
  The 0.8B model's **scout** role completed naturally (`finish_reason: "stop"`,
  717 tokens), returned valid JSON, and named the seeded defect in
  `task_contract.py` with the correct file and 0.95 confidence. The first
  `comparison.json` was also produced from real data, and the cost fix is visible
  in it: `harness_owned_seconds` of `0.000155` against `model_wall_s` of `33.8`,
  where before the fix that field carried the model's own latency.

  Neither local model can produce a unified diff — the 3B model answers with
  `<tool_call name="bash">`, the 0.8B model returns the whole file where a diff
  belongs — so no candidate patch has been produced by a model and the acceptance
  path has still never been driven by real model output. The harness specifies the
  format explicitly, so this is a model capability limit and `TRUNCATED` is the
  honest verdict.

### Fixed

- **The documented invocation could not evaluate a patch at all.** `--out` defaults
  to `<repo>/runs` and the operator example uses `--out ./runs`, but the scratch
  evaluation worktree was derived from the evidence root — and
  `EvaluationWorktree` refuses to build one inside the harness checkout, the
  fixture or the bound source, correctly. So every stage that actually evaluates
  a candidate failed with `WorktreeGuardError` under the documented invocation.

  Nothing caught it. Every test puts its run directory in a temporary directory
  outside the repository, and every live attempt so far died at role parsing before
  `evaluate` was reached, so this was the first time the live path got as far as
  creating a worktree. It surfaced from running the operator's own example.

  The guard is untouched and still absolute. The runner now refuses to aim at it:
  the scratch root moves to a temporary directory and the move is recorded on
  every attempt, because where the evaluation copy lived is part of the evidence.

- **A roll-up pooled totals across harness revisions without saying so.** The
  harness refuses to pool across artifact *schemas*, so a legacy `bench_*` run can
  never be silently mixed into a `realtask.*.v1` total — but the same discipline
  was missing one level in, across revisions *within* a schema. That is not
  theoretical: the meaning of `harness_overhead_s` changed when the cost
  double-count was fixed, so a roll-up spanning that fix sums a number that meant
  one thing before and another after. Found by running `summarize` over the first
  real evidence directories, where the totals carried 53.95s of harness overhead
  that was really model latency. The roll-up now carries a structured
  `mixed_harness_revisions` flag and an additive scope limit naming the revisions
  and pointing a reader at the per-run `metrics.json` instead.

- **Swarm evidence reported the wrong candidate's safety verdict.**
  `_merge_review` copied a hand-listed set of patch fields from the review
  attempt into the swarm attempt, and `safety_ok` / `safety_reason` were not on
  the list. A swarm whose reviewer asked for changes and whose refinement was then
  refused by the screen produced evidence that contradicted itself:
  `apply_reason` explained the symlink refusal in full while `safety_reason` sat
  empty. Every other field correctly described the final candidate. Found by
  scripting exactly that swarm; the screen itself worked, and no symlink reached
  any worktree.
- **The refinement ceiling had no test.** `--max-refinements` is documented as
  able to lower the budget and never raise it, enforced by `RunnerOptions.clamp`
  and a second clamp in the CLI. Nothing checked either, so a refactor dropping
  one would make a documented safety ceiling silently raisable. Both are now
  tested, including that the CLI flag itself is not the last line of defence.

  Probing this one produced a false positive first: `RunnerOptions(
  max_refinements=99)` returns 99 because `clamp()` has to be called, and the
  runner is what calls it. The bug was in the probe, not the code.
- **A model asking for a tool was filed as `TRUNCATED`.** The second live run,
  against a tool-tuned model, answered with a 900-token
  `<tool_call name="read_file">` whose `call_id` padding consumed the whole
  budget, and the attempt was recorded as `TRUNCATED` with the note "contained no
  JSON object". Both statements are true, and together they send an operator to
  the token budget when the cause is a role-contract violation — the contract
  already opens with "You have NO tools, NO shell and NO filesystem access". New
  outcome `TOOL_CALL_REQUESTED`, detected from the reply and ranked ahead of
  truncation because the harness orders outcomes by which fact is actionable.
  The same run now reports `outcome=TOOL_CALL_REQUESTED` and names the tool.
- **The model's own wall time was billed to the harness a second time.** Nine
  call sites wrapped `self._call` in an overhead window, so the model's latency
  was added to `harness_overhead_s` as well as to `model_wall_s`, and
  `total_wall_s` came out at exactly twice the model time. Found by the first
  live run, where `overhead/model` was `1.00` to two decimal places; the same
  measurement after the fix is `0.00`. This corrupted
  `controller_integration_overhead.harness_owned_seconds` and the cost difference
  between single and swarm — the numbers the comparison artifact exists to report.

  No test could have caught it: `ScriptedAdapter` reports a constant 1.0s per
  call, so scripted runs have never had a realistic model wall time.
  `OverheadAccountingTests` uses an adapter that reports real elapsed latency and
  asserts overhead stays below model time, that total is not double-counted, and
  that a three-role swarm does not bill its calls twice. All three fail with the
  fix reverted.
- **The best-single ranking had no completeness check.** `_select_best_single`
  falls back to rank 99 for any outcome missing from its table. That is a safe
  default only while the table happens to match the `Outcome` enum, and
  completeness is not a property the type system enforces: add a member and it
  silently sorts worse than every real outcome, so the comparison artifact can
  name the wrong attempt as best, with nothing else going red. Two tests now
  assert the table covers every outcome exactly once with contiguous ranks, and
  pin the orderings that are judgement calls — `SUCCESS` beats everything,
  `TIMEOUT` beats `SOURCE_MISMATCH` because an unbound fixture is worse
  evidence than a hang, and a harness bug is worse than a wrong answer.
  Verified by adding a `PARTIAL_SUCCESS` member and watching the check fail.
- **Acceptance depended on whether Pillow happened to be installed.**
  `cli._brand_check` opens with `from PIL import Image` before it inspects
  anything, so the campaign fixture's broader tier raised `ModuleNotFoundError`
  on a host without Pillow and reported `REGRESSION_FAILURE` for a correct
  patch — 24 tests red in CI, none reproducible locally. The oracle now stubs
  `PIL`, matching the `pydantic_settings` stub the auto-router fixtures already
  used. Two tests keep it fixed: one runs the broader tier with `PIL` made
  genuinely unimportable and asserts 14 passed, the other asserts the blocker
  actually blocks.
- **`UndeclaredDependencyTests`** — every non-stdlib import in a frozen snapshot
  must now be stubbed by that fixture's oracles or recorded in
  `UNREACHABLE_DEPENDENCIES` with a specific reason. A new dependency cannot be
  added quietly, and a bare module name does not satisfy the check.
- **A fixture oracle I wrote was itself racy.**
  `test_index_score_is_updated_at_in_milliseconds` compared the index score
  captured at write time against a *second* `_now_ms()` call, so it failed
  whenever the clock ticked over between them — one CI run in about twenty, on
  one Python version, reproducing nowhere. It now compares the score against the
  record's own `updated_at`, plus a magnitude bound and a one-minute freshness
  bound. Under an adversarial clock the old formulation mismatched on 400 of 400
  straddling ticks; the new one reads the clock zero times in the equality.
- **Acceptance failures said only that a boolean was false.**
  `assertTrue(tests.broader_all_passed)` on a fourteen-check suite costs a bisect
  through a four-minute run to locate. `assertTestsPassed` now fails with the
  command line, return code, and the tail of stdout and stderr. This is what made
  the Pillow failure diagnosable from a CI log at all.

### Added

- **`.github/workflows/realtask.yml`** — the harness had 345 tests and no CI, so
  every guarantee in this file was enforced only on one machine. The workflow
  runs on every push and pull request, on Python 3.11 and 3.12: fixture
  validation, a per-fixture seal check that names which fixture went stale,
  `unittest discover`, `pytest`, and a guard that the run left no tracked file
  modified. It needs no secrets. `REALTASK_ENDPOINT` and
  `REALTASK_ENDPOINT_BASE_URL` are set to empty strings on purpose, so a test
  that ever starts depending on a live endpoint fails instead of quietly
  succeeding against whatever the runner exposes.

- **Three cross-repository fixtures** — the corpus no longer derives from a single
  defect. All three were found by reading frozen source and all three fail on
  their own untouched snapshot:
  - `auto_router_task_contract_lane_mismatch` (`bug_fix`) — frozen from
    `scottjoyner/auto-router@7575d2b`. `task_contract.py` derives `requires_tools`
    from a hand-maintained set covering four tool kinds while the plan and metrics
    vocabularies cover ten, so ten plan lanes can never be routed. The reference
    repair gives the vocabularies a single home. Targeted 29 failed / 13 passed
    before, 42 passed after; broader 27 passed either way.
  - `auto_router_settings_latency_cache_path` (`bug_fix`) — same repository.
    `settings.py` re-implements the SQLite URL grammar inline for the latency
    cache path, so the bare relative form `sqlite:///data/x` resolves one way and
    is rejected the other. The reference repair factors out `_resolve_sqlite_path`.
    Targeted 2 failed / 11 passed before, 13 passed after; broader 20 passed
    either way.
  - `assistx_answers_store_cursor_drops_ties` (`bug_fix`) — frozen from
    `scottjoyner/auto-assist@e872ed64`. `list_answers_paginated` keys pages on a
    Redis score that is `updated_at` in whole milliseconds, so it is not unique,
    but the cursor uses only the score half and an exclusive bound: any answer
    sharing a millisecond with the page boundary is skipped permanently. The
    reference repair makes the bound inclusive and resumes from the last member
    examined rather than the last fetched. Targeted 8 failed / 8 passed before,
    16 passed after; broader 30 passed either way.
- **`test_realtask_corpus.py`** — corpus-level guarantees that no individual
  fixture test can make. Fails if a fixture is unsatisfiable (no reference
  solution reaches `SUCCESS`), undiscriminating (the untouched snapshot passes),
  or non-independent (names another fixture, is byte-identical to a fixture from
  another repository, or the campaign collapses onto one defect or one family).
  Also requires every `patch` deliverable to have a tracked reference solution,
  checks collateral damage reads as `REGRESSION_FAILURE` rather than success, and
  scans for credential literals instead of credential vocabulary.
- **`test_realtask_reference_*.diff`** — reference solutions as reviewable
  version-controlled artifacts rather than inline test strings. The collateral
  file for the campaign fixture repairs the defect *and* changes two unrelated
  defaults, which is what proves the broader tier has teeth.

- **`test_realtask_reference_review.json`** / **`test_realtask_reference_contract.json`**
  — reference answers for the two `analysis` fixtures. An analysis deliverable is
  prose, so its reference is an answer; without one there was no proof its grader
  could be satisfied at all, and the corpus test could not check satisfiability
  for half the corpus.

### Fixed

- **A symlink patch escaped the evaluation worktree.** The patch safety screen
  screened path strings -- not absolute, no `..`, declared or under a writable
  prefix -- but a patch can create a symlink satisfying all three while pointing
  anywhere on the host. `git apply` creates the link and pytest imports what it
  collects, so the target's module body executed at collection time, outside the
  worktree and outside the model's read-only guarantee. Verified end to end
  before fixing: the linked module's import-time side effect ran.
  `screen_patch` now admits only file modes `100644` and `100755`, refusing
  `120000` (symlink) and `160000` (gitlink) and naming the mode in octal.
  New `test_realtask_containment.py`; four of its five tests fail with the mode
  screen neutralised, and one of them proves the linked module really is
  imported, so the non-execution check cannot pass for the wrong reason.
- **A safety-screen refusal was reported as a clean screen.** The refusal path
  never set `safety_ok=False` or `safety_reason`, so evidence claimed
  `safety_ok: true` for a patch the screen had just refused, making a
  containment refusal indistinguishable from a parse error. Both now travel
  through `ApplyResult`, along with the offending modes and paths.
- **Generated-but-tracked corpus artifacts could silently drift.** The
  test_generation reference solution is a copy of the campaign oracle, the
  campaign reference diff is a copy of the inline `REFERENCE_REPAIR`, and the two
  analysis graders share a hardening block. All three were consistent only
  because they happened to be regenerated in the same commit that changed their
  source. `DerivedArtifactTests` regenerates each one and compares, naming both
  the artifact and its source in the failure. Neither drift mode announces itself
  otherwise: a desynced reference stops discriminating, or one grader becomes
  passable by keyword stuffing again while every other test stays green. Both
  failure modes were verified before the check was trusted.
- **The campaign oracle was passable by sniffing its own test double.** A patch
  that planned early only when `type(driver).__name__ == "FakeDriver"` passed
  9/9 targeted checks and left the real defect completely intact -- for that
  stub the behaviour genuinely is correct, so no behavioural assertion could
  catch it. The double is now instantiated under three class names
  (`FakeDriver`, `_GraphDriverStandIn`, `Session`), so a repair has to hold for
  every name rather than one remembered from reading the oracle. The attack now
  fails 10 checks, the frozen snapshot fails 15, the reference passes all 27.
  `test_realtask_overfit_live_driver.diff` keeps the attack.
- **The settings oracle was passable by memorisation.** A patch that
  special-cased the single literal URL the fixture names
  (`sqlite:///data/router.sqlite3`) and changed nothing else passed 13/13
  targeted and 20/20 broader, because every URL in the fixture shared one
  basename. The SQLite grammar stayed as divergent as before for every filename
  the fixture did not name. The oracle now also sweeps five filenames and mount
  points that appear nowhere else in it; the memorisation patch fails 5 of them,
  the frozen snapshot fails 7, and the reference repair passes all 18.
  `test_realtask_overfit_settings.diff` keeps the attack, and
  `test_realtask_corpus.py` asserts it is rejected, applies cleanly, and is
  never filed as the reference solution.
- The answers-store page-size sweep was widened from `[1, 2, 3, 5, 7]` to
  `[1, 2, 3, 5, 7, 11, 100]`. The first set was already chosen to be useless as a
  lookup table; the last two cover the sizes the broader tier reaches for.
- **Both analysis graders were passable by keyword stuffing.** `check_answer.py`
  scanned the entire captured answer for trigger substrings, so a 391-byte word
  salad containing every trigger and no explanation passed all six findings in
  `code_review`. A finding must now be carried by a single proposition, and that
  proposition must contain at least three words the grader is not keyed on.
  `test_realtask_corpus.py` rebuilds the attack from each grader's own tables and
  asserts the reference answers pass while the dump, the scattered triggers and an
  empty answer all fail — through the grader and through the runner.
- Three generated reference diffs carried a doubled strip prefix
  (`--- a/a/...`, `+++ b/b/...`) and would not apply with `-p1`. `test_realtask_corpus.py`
  now rejects that shape.

## v2.0.0 - Real-Task Benchmark Harness (2026-10-02)

### Added

- **`realtime_bench.py`** — the canonical entrypoint for frozen real-task
  benchmarks and bounded swarm experiments. Stages: `single`, `scout`,
  `implement`, `review`, `swarm`. See `docs/REAL-TASK-BENCHMARK.md`.
- **`realtask/`** — the harness package: frozen `RealTask` fixtures, strict
  fixture validation, SHA-256 source binding with fail-closed `SOURCE_MISMATCH`,
  structured SCOUT/IMPLEMENTER/REVIEWER contracts, a patch safety screen, a
  disposable evaluation worktree, allow-listed command execution, component
  metrics, atomic evidence writes, and a single-vs-swarm comparison artifact.
- **`realtask/tasks/`** — five frozen fixtures covering `bug_fix`,
  `test_generation`, `code_review`, `small_refactor` and `contract_reasoning`.
  The campaign fixture freezes the `auto-ingest` driver-lifetime regression
  (`planner.plan_shorts(..., driver=driver)` executed after `driver.close()`)
  from `scottjoyner/auto-ingest@d7d75ff`, and is the only fixture with a
  broader regression tier: a 14-check suite over the rest of the module that
  passes both before and after the canonical repair.
- **`seal_realtask_fixture.py`** — freezes a fixture: recomputes per-file
  SHA-256, writes `source-manifest.json`, stamps `task.json`. `--check` verifies
  without writing.
- **`EVIDENCE-PROVENANCE.json`** / **`EVIDENCE-PROVENANCE.md`** — machine-readable
  registry separating the historical `bench_*` / `bench_v*` / `benchmark_*` /
  `dual_*` / `cpm_tb2_*` artifacts from the new `realtask.*.v1` family, with the
  producing script, output glob, capabilities and gaps for every entry.
- **`docs/HARNESS-REGISTRY.md`** — capability and call-site map of every existing
  benchmark script, the reconciliation decision, what was carried forward, and
  the conclusively-dead-code findings.
- **`docs/REAL-TASK-BENCHMARK.md`** — the harness guide.
- **`test_realtask_*.py`** — 220 tests across fixtures/binding, roles, patches,
  the runner, evidence, endpoint configuration and the CLI (including a full run
  against a loopback OpenAI-compatible endpoint). The 24 pre-existing
  `test_fixgit_repro_v1.py` cases still pass unmodified.

### Design decisions

- **One canonical entrypoint, nothing retired.** No `fast_bench_v8.py`. Every
  existing script still runs and still writes its own artifact format.
  `realtime_bench.py` was introduced because no existing script could bind a
  frozen task to a source revision, apply a candidate patch safely, enforce role
  contracts, or write versioned atomic evidence.
- **Models are read-only.** A model returns text. The harness screens the patch,
  applies it only inside a disposable worktree it refuses to place inside any
  read-only tree, runs allow-listed argv commands there with no shell, then
  discards it. The authoritative source is never opened for writing.
- **Eleven-outcome failure taxonomy** with a documented precedence order, and a
  candidate-scoped versus attempt-level split so a refinement round is judged on
  its own final validation without erasing history.
- **Component metrics, never a composite score.** The comparison artifact
  compares components to components and states its scope limits in the artifact.
- **At most one refinement per attempt**, as a hard ceiling the CLI cannot raise.
- **Endpoint identity is operator-supplied.** No node names or addresses are
  hardcoded; a test enforces it. The harness never discovers or starts a server.
- **Independent of production scheduling.** No AssistX claiming, no task
  completion, no auto-router registration, no runtime projection, no dispatch, no
  push to any benchmark target repository.

### Post-release additions

- **`summarize`** — a cross-task roll-up (`realtask.rollup.v1`). Goal step 4 is
  "feed qualification evidence onward"; one run produced one comparison and
  nothing folded a campaign together. Reports outcome histogram, per-family and
  per-strategy breakdowns, retained per-attempt components, totals, and which
  harness shas and runtimes produced it. No composite score, no verdict
  language, token totals nulled rather than partially summed, and corrupt or
  foreign-schema runs skipped rather than fatal.
- **`--scout-file`** — hands a recorded scout result to the implementer, so a
  scout can be read and judged between invocations instead of re-asking the
  model. Validated as evidence; recorded in the manifest with its provenance.
- **Adapter resilience** — the capability degradation ladder (`stream_options` →
  `seed` → streaming) and a bounded transient retry, both recorded per call as
  `request_profile` and `retries`. See the guide for why it stops at streaming.

### Follow-up: gaps closed by review

A second pass over the specification against the first implementation found four
things claimed but not actually exercised, plus two real bugs. All are fixed:

- **No fixture exercised `broader` acceptance**, so `REGRESSION_FAILURE` was
  unreachable. The campaign fixture now carries a broader tier, and a crafted
  "correct repair that also changes two defaults" patch proves the classification.
- **`endpoint_from_config_file` and `endpoint_from_env` were untested**, and the
  harness docstrings named specific nodes while claiming not to. The docstrings
  now use generic placeholders and a test asserts no node name or address appears
  anywhere in `realtask/` or the entrypoint.
- **Standalone `--stage scout|implement|review` was untested**, and
  `--stage review` crashed writing evidence (`'review'` was not an evidence
  directory). Strategies now map explicitly onto evidence directories.
- **A multi-task run let the last task overwrite earlier evidence**, and every
  comparison was written under the final task's id. Each task now gets its own
  `tasks/<task_id>/` subtree and its own `comparison.json`; the manifest indexes
  the whole tree.
- Evaluation work trees were created inside the evidence root as `work/`, which
  made a listing of that root ambiguous. They now live in a per-run sibling
  `<run_id>.work/` directory and are removed when the run ends.

A third pass found four more unproven claims and three more real defects:

- **The always-emit invariant was claimed but never implemented.** The registry
  listed it as carried forward from `fixgit_repro_v1.py:178-199`; the runner had
  no crash handling at all, so one unexpected exception lost every artifact for
  that task. Attempts now run under a guard: an unexpected failure terminates
  that attempt with a recorded outcome and `harness_error`, evidence is still
  written, later tasks still run, and the process exits 3 so a controller can
  tell a bad harness from a bad model.
- **`REGRESSION_FAILURE` needed a broader tier, which then needed the real
  `models` module**, so the campaign fixture now snapshots five source files
  rather than two.
- **A hung acceptance command was reported as `TARGETED_TEST_FAILURE`.** Not
  finished and finished-and-failed are different findings; a timed-out
  acceptance command is now `TIMEOUT`, which the precedence order already ranked
  correctly.
- **`git_head` could bind to a parent repository.** `git rev-parse` walks up, so
  a `--source-root` nested inside an unrelated checkout reported that checkout's
  HEAD. The source root must now be a repository root itself.
- **Attempt ids collided.** Repeated attempts of one strategy all used
  `<task>::<strategy>`, making the best-single lookup ambiguous. Ids now carry an
  ordinal.
- **`--single-attempts`, `--max-source-bytes` and `--require-head` were
  untested** despite all being documented. All three now have coverage.

### Known limitations

- `test_generation` executes candidate-authored test code. It runs only inside
  the disposable worktree with a scrubbed environment, and this is disclosed in
  the fixture manifest and the guide.
- Token counts are `null` when an endpoint omits `usage`; they are never
  estimated. TTFT is `null` under `--no-stream`.
- Broader acceptance commands run only when targeted acceptance passes, and the
  skip is recorded rather than hidden.

---

## Version 1.0.0 (2026-09-11)

### Initial Release

**Models:**
- MiniCPM5-2B LoRA (Port 1235): 10.5 tok/s, 8/8 tool calling
- VibeThinker-3B (Port 1234): 5.7 tok/s, reasoning only

**Features:**
- Dual-model synergistic harness
- Task routing based on request type
- systemd services for both models
- Basic benchmark suite

**Performance:**
- Tool calling: 100% accuracy (8/8)
- Average generation speed: 10.5 tok/s
- Average prompt speed: 23-33 tok/s

**Known Issues:**
- VibeThinker doesn't output tool calls directly
- Ling3.0-tiny too slow for CPU (1.5 tok/s)
- iGPU doesn't support 16-bit storage

---

## Roadmap

### Version 1.1.0 (Planned)
- [ ] Improve VibeThinker tool calling with better prompts
- [ ] Add multi-turn conversation support
- [ ] Implement feedback loop for self-improvement
- [ ] Add more edge case tests

### Version 1.2.0 (Planned)
- [ ] Create task-specific LoRAs
- [ ] Implement dynamic load balancing
- [ ] Add conversation history sharing
- [ ] Optimize memory usage

### Version 2.0.0 (Future)
- [ ] Multi-node distribution (OptiPlex + xwing)
- [ ] Automatic LoRA fine-tuning from traces
- [ ] Specialized model routing
- [ ] Production-ready error handling

---

## Benchmark History

### 2026-09-11: Initial Benchmark

| Model | Tool Calling | Speed | RAM |
|-------|--------------|-------|-----|
| MiniCPM5 LoRA | 8/8 (100%) | 10.5 tok/s | 2.8GB |
| VibeThinker | 0/8 (0%) | 5.7 tok/s | 1.5GB |
| Ling3.0-tiny | Not tested | 1.5 tok/s | 3.5GB |

**Hardware:** OptiPlex-9030-AIO (i5-4590S, 7.7GB RAM)

---

## Model Evaluation

### MiniCPM5-2B LoRA
- **Strengths:** Excellent tool calling, fast generation
- **Weaknesses:** Requires LoRA adapter for tool calling
- **Best for:** Tool execution, function calling
- **Status:** Production ready

### VibeThinker-3B
- **Strengths:** Good reasoning, smaller footprint
- **Weaknesses:** No native tool calling, slower
- **Best for:** Planning, evaluation, reasoning
- **Status:** Needs improvement for tool calling

### Ling3.0-tiny
- **Strengths:** Large model (7.9B), MoE architecture
- **Weaknesses:** Too slow on CPU, requires newer llama.cpp
- **Best for:** Complex reasoning (with GPU)
- **Status:** Not recommended for OptiPlex

---

## Testing Protocol

### Benchmark Categories
1. **Tool Calling:** bash, read, write, search operations
2. **Reasoning:** Explanation, comparison, debugging
3. **Multi-step:** Sequences, conditions, loops
4. **Edge Cases:** Empty, long, special characters, unicode

### Metrics
- **Success Rate:** % of tasks completed correctly
- **Generation Speed:** Tokens per second (output)
- **Prompt Speed:** Tokens per second (input)
- **Memory Usage:** RSS in MB

### Hardware Requirements
- **Minimum:** 4GB RAM, 4-core CPU
- **Recommended:** 8GB RAM, 8-core CPU
- **Optimal:** GPU with 8GB+ VRAM
