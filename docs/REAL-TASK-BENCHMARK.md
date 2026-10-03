# Real-task benchmark + bounded swarm experiment harness

`realtime_bench.py` runs one experiment:

```
real repository task  ->  one model attempt  vs  role-separated scout /
implementer / reviewer attempts
```

It produces evidence. Deciding what to do with that evidence belongs to an
authoritative controller such as Hermes.

Design rationale and the reconciliation of the older scripts:
[`HARNESS-REGISTRY.md`](HARNESS-REGISTRY.md).
Evidence provenance: [`../EVIDENCE-PROVENANCE.json`](../EVIDENCE-PROVENANCE.json).

---

## 1. The one command an operator needs

```bash
cd /path/to/agent-harness

# 1. prove the fixtures without any endpoint
python3 realtime_bench.py validate

# 2. point the harness at one already-running endpoint and run frozen real tasks
python3 realtime_bench.py run \
  --task auto_ingest_plan_shorts_live_driver \
  --stage single --stage swarm \
  --base-url http://<host>:<port>/v1 \
  --model <model-id> \
  --label <operator-label> \
  --out ./runs

# 3. read the evidence
cat ./runs/<run_id>/comparison-auto_ingest_plan_shorts_live_driver.json
```

`plan-command` prints that invocation for you:

```bash
python3 realtime_bench.py plan-command \
  --task auto_ingest_plan_shorts_live_driver \
  --base-url http://<host>:<port>/v1 \
  --model <model-id> \
  --label <operator-label>
```

`--label` and `--node` are free-form operator strings. They are recorded in
evidence for attribution and are never interpreted. The harness has no list of
known runtimes and must never grow one: a test asserts that no node name or node
address appears anywhere in `realtask/` or in the entrypoint, including in
documentation examples.

The endpoint may equally be supplied by `--endpoint-config <file>` (a private
JSON file; the API key is named indirectly via `api_key_env` so it never lives in
the file) or by `REALTASK_ENDPOINT_BASE_URL` / `REALTASK_ENDPOINT_MODEL`. A test
asserts all three inputs produce identical endpoint identity.

The harness does **not** discover hosts, probe subnets, or start servers. It
POSTs to the URL it is given. Exit codes: `0` clean, `1` bad arguments or
fixtures, `2` an integrity failure (the authoritative source changed), `3` a
harness error — see §9a.

## 2. Stages

| Stage | Calls | Shape | Evidence dir |
|---|---|---|---|
| `single` | 1 | one model, full bounded task context, one reply | `single/` |
| `scout` | 1 | locate and explain the defect | `scout/` |
| `implement` | 1 | produce a candidate patch, which is then evaluated | `implementer/` |
| `review` | 1 (+1) | adjudicate a patch supplied via `--patch-file`; optionally spend the single refinement | `reviewer/` |
| `swarm` | 3 or 4 | scout → implement → evaluate → review → optional ONE refinement → final validation | `swarm/` |

Every stage runs standalone as well as inside `swarm`. `--stage review` requires
`--patch-file` because there is nothing to review otherwise.

`swarm` in full:

```
scout                                  (1 bounded call)
  → implementer  receives task + scout result
  → harness applies the candidate patch inside a disposable worktree
  → harness runs allow-listed acceptance commands
  → harness re-verifies the source binding
  → reviewer    receives the exact patch + exact binding + exact test evidence
  → ONE refinement round, if the reviewer said "revise"
  → final validation
```

There is no unbounded agent conversation and no model-driven tool loop. Every
role call is single-turn and bounded. The refinement budget is a hard ceiling of
one per attempt; `--max-refinements` can lower it and can never raise it.

## 3. Frozen fixtures

A fixture is a self-contained, reproducible unit of work:

```
realtask/tasks/<task_id>/
    task.json               # the frozen manifest
    source-manifest.json    # authoritative per-file SHA-256 manifest
    source/                 # bounded snapshot, repo-relative paths
    tests/                  # bounded acceptance tests, run against a copy
```

`task.json` declares:

| Field | Meaning |
|---|---|
| `task_id` | stable identifier |
| `task_family` | one of `bug_fix`, `test_generation`, `code_review`, `small_refactor`, `contract_reasoning` |
| `deliverable` | `patch` or `analysis` |
| `problem` | the statement given to the model |
| `constraints` | hard rules; violating one is a contract violation, not a style note |
| `relevant_source_files` | files the answer must engage with |
| `prompt_source_files` | files actually shown to the model (defaults to the above) |
| `known_regression` | optional description of what regression this guards |
| `source` | repository, HEAD, manifest hash, per-file SHA-256, writable prefixes |
| `acceptance` | targeted/broader argv vectors, allow-list, content assertions |

Loading is strict. Unknown keys, unknown families, unknown deliverables, absolute
paths, path traversal, relevant files absent from the source set, and fixtures
with no acceptance commands are all rejected. A fixture the harness cannot fully
understand is never benchmarked.

After editing a snapshot, re-seal it:

```bash
python3 seal_realtask_fixture.py realtask/tasks/<task_id>
python3 seal_realtask_fixture.py realtask/tasks/<task_id> --check   # verify only
```

### Source binding

Before any model call the harness verifies:

1. `source-manifest.json` hashes to the value recorded in `task.json`;
2. every declared file is present;
3. every declared file hashes to its recorded SHA-256;
4. the source HEAD matches.

Any failure is `SOURCE_MISMATCH` and the run stops with zero model calls. There
is no "continue anyway" path and no fallback to a different clone.

Two modes:

- **snapshot** (default, hermetic): the fixture's own frozen snapshot. The
  recorded HEAD is provenance and the evidence says so explicitly
  (`head_status: recorded_provenance`, `actual_head: null`). Nothing outside the
  fixture is needed.
- **external worktree** (`--source-root PATH`): verify a real checkout. HEAD is
  compared with `git rev-parse HEAD` and a mismatch fails closed. This is the
  mode that refuses to benchmark a different clone.

`git rev-parse` walks up parent directories, so a source root merely *nested
inside* an unrelated checkout would otherwise report that checkout's HEAD and
appear to bind. The harness therefore requires the source root to be a repository
root itself; anything else fails closed with an explicit reason.

`--require-head` additionally forces live HEAD verification in snapshot mode
(used when the snapshot is itself a git checkout).

This *consumes* the source-binding concept. It grants no execution authority and
performs no claiming; task authority lives in auto-assist and auto-router.

## 4. Role contracts

No role is ever asked for hidden reasoning, chain of thought, or a scratchpad.
A test enforces the absence of such asks.

**SCOUT** → `root_cause`, `relevant_files`, `plan`, `risks`, `confidence`

**IMPLEMENTER** → `patch`, `tests`, `assumptions`, `confidence`

**REVIEWER** → `defects`, `missing_coverage`, `contract_violations`,
`verdict` (`accept` | `revise`), `confidence`

Each role replies with exactly one JSON object. Parsing is tolerant of formatting
(fenced blocks, surrounding prose, escaped newlines) and strict about content: a
missing field, an out-of-range confidence, or a verdict that is not exactly
`accept` or `revise` is a `PROTOCOL_FAILURE`. An output that ran out mid-structure
is `TRUNCATED`, which is a different finding.

## 5. Models are read-only

A model receives bounded source text and returns text. It never receives a tool,
a shell, or a filesystem handle. The harness owns every mutation:

1. the candidate patch is screened against the fixture's declared file set and
   its writable prefixes; anything else is refused, including parent traversal
   and absolute paths;
2. it is applied inside a **disposable worktree** created under the output root.
   The harness refuses to build that worktree inside the bound source, the
   fixture, or the agent-harness checkout;
3. allow-listed acceptance commands run there, with no shell;
4. the worktree is discarded.

The authoritative source tree is never opened for writing. The runner
fingerprints the whole fixture before and after each task and records
`authoritative_source_unchanged` in `manifest.json`; a `false` there exits the run
with a non-zero status.

### Executing candidate-authored code

`test_generation` is the one family where the candidate's output is code that the
harness executes — that is the task. Those runs:

- execute only inside the disposable worktree;
- run with a scrubbed environment: endpoint configuration and any variable whose
  name contains `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `APIKEY` or `_API_KEY`
  is removed;
- are disclosed in the fixture manifest and in this document.

## 6. Acceptance is grounded, never a model judgement

Acceptance commands are argv vectors, never shell strings. The program of each
argv must resolve to the interpreter running the harness or to a program the
fixture explicitly allows; `bash`, `sh` and `curl` are refused by default. Three
placeholders are substituted: `${PYTHON}`, `${WORKTREE}`, `${ANSWER}`.

A fixture declares two tiers. `targeted` is the acceptance gate: it must all
pass. `broader` is the regression guard — everything the candidate could plausibly
break while fixing the defect — and runs only after targeted acceptance passes.
When it is skipped, `test-evidence.txt` records
`BROADER ACCEPTANCE: not run (targeted acceptance failed)` rather than silently
omitting it.

For `deliverable: analysis` the patch slot is empty and the candidate's answer
text is written to `_realtask_answer.txt` so a fixture-provided checker can
assert on it.

The harness never lets a model decide whether a task succeeded. Test evidence
does. This is the `run_dual_4.py:32` pattern generalised.

## 7. Failure taxonomy

Every attempt ends with exactly one of:

| Outcome | Meaning |
|---|---|
| `PROTOCOL_FAILURE` | output could not be parsed into the role schema |
| `GROUNDING_FAILURE` | the answer did not engage with the bound source |
| `EMPTY_OUTPUT` | no usable content |
| `TRUNCATED` | output ended early |
| `TIMEOUT` | a bounded resource was exhausted: a model call exceeded its deadline, or an allow-listed acceptance command exceeded the test timeout |
| `INVALID_PATCH` | not a well-formed unified diff, or refused by the safety screen |
| `PATCH_DOES_NOT_APPLY` | well formed, but does not apply to the bound source |
| `TARGETED_TEST_FAILURE` | targeted acceptance did not all pass |
| `REGRESSION_FAILURE` | broader acceptance regressed |
| `SOURCE_MISMATCH` | binding verification failed; the run was refused |
| `REVIEW_REJECTED` | reviewer asked for changes and no refinement budget remained |
| `SUCCESS` | targeted acceptance passed with no recorded regression |

When several conditions hold, the first match in `OUTCOME_PRECEDENCE` wins, so a
run is reproducible regardless of execution order. The order is by specificity:
patch-mechanics failures outrank grounding, because "you did not produce a
usable diff" is the actionable fact.

**Candidate-scoped outcomes vs attempt-level outcomes.** An attempt's terminal
verdict is decided by its final candidate. When the refinement round produces a
new candidate, candidate-scoped outcomes (`INVALID_PATCH`,
`PATCH_DOES_NOT_APPLY`, `TARGETED_TEST_FAILURE`, `REGRESSION_FAILURE`,
`REVIEW_REJECTED`) are cleared from the terminal set while remaining in
`outcomes_seen`. Attempt-level problems — `SOURCE_MISMATCH`, `PROTOCOL_FAILURE`,
`TIMEOUT`, `GROUNDING_FAILURE` — are never cleared: they describe the attempt,
not one candidate.

So a reviewer saying `revise` does not poison an attempt whose refinement then
passes final validation, and `metrics.json` still shows the reviewer rejected the
first candidate.

## 8. Metrics

Quality is never collapsed into a single number. Every component stays
addressable:

- source and task identity: manifest SHA, snapshot SHA, fixture SHA, binding
  SHA, source HEAD
- node/model/runtime identity per call, plus role
- per call: wall time, TTFT, tokens/sec, input tokens, output tokens, total,
  whether usage was actually reported, finish reason, server timings
- grounding: expected files, files mentioned, files mentioned that exist in the
  bound source, hallucinated files, overlap, required-file satisfaction
- patch: produced, extracted, extraction strategy, safety, applied, applier,
  files changed, files the role declared it would change, unnecessary changed
  files, syntax
- tests: every command, argv, return code, timeout flag, duration, and the tail
  of stdout/stderr
- review: verdict, confidence, defects (with severities), missing coverage,
  contract violations, blocking defect count, unaddressed-after-refinement
- outcome, outcomes seen, refinement count, harness overhead

Token counts are `null` when the endpoint does not report `usage`. They are never
estimated. TTFT is `null` when streaming is disabled with `--no-stream`.

Attempts are separately identified as `<task_id>::<strategy>`, with an ordinal
suffix when a strategy runs more than once, so `metrics.json`,
`TaskMetrics.attempt()` and the comparison's best-single lookup all address the
same attempt unambiguously.

The single grounding scalar, when present, is
`expected_overlap * (1 - hallucinated_rate)`. It exists for ranking runs and is
never used as a task verdict on its own.

## 9. Evidence

```
runs/<run_id>/
    manifest.json                  harness git SHA + dirty flag, fixture SHA,
                                   endpoint identity, argv, host, options,
                                   timestamps, integrity, authority assertions,
                                   and an index of every artifact written
    task.json                      the frozen fixture, verbatim
    source-manifest.json           the frozen manifest, verbatim
    source-manifest.verified.json  that manifest plus the binding verdict
    single/  scout/  implementer/  reviewer/  swarm/
        result.json                parsed role results + binding + notes
        metrics.json               that attempt's component metrics
        <role>.txt                 the model's raw reply, verbatim
        test-evidence.txt          the exact acceptance transcript
    patch.diff                     every candidate patch, verbatim and labelled
    test-results.json              every command and its output, per attempt
    metrics.json                   all attempts
    comparison.json                single vs swarm, component-wise
```

Running several tasks in one invocation (`--task` repeated) keeps every artifact
above but nests each task's evidence under `tasks/<task_id>/`, so one task can
never overwrite another's. `manifest.json` stays at the run root and indexes the
nested artifacts.

Evaluation work trees are scratch, not evidence. They are created under a
per-run sibling directory (`<out>/<run_id>.work/`) and removed when the run
finishes, so a listing of the evidence root shows runs and nothing else.

Every write is atomic: temp file in the destination directory, `flush`, `fsync`,
`os.replace`, then `fsync` on the directory. A reader never sees a half-written
artifact and a crashed run leaves either the previous file or the complete new
one. Adapted from `fixgit_repro_v1.py:77-95`, the only pre-existing atomic writer
in this repository.

API keys are never written. The endpoint identity records `api_key_set: true|false`
and nothing else.

## 9a. When the harness itself fails

A harness bug, a full disk, an unreadable fixture: none of those are the model's
fault, and none of them should erase the attempts that already succeeded.

- Every attempt runs under a guard. An unexpected exception terminates that
  attempt with a recorded outcome and a populated `harness_error`, and the run
  continues to the next task.
- Evidence is still written: `metrics.json`, `test-results.json` and the per-role
  directories all appear, with `harness_error` set.
- If writing the task-level artifacts themselves fails, the failure is recorded
  in `artifact-write-failure.json` rather than swallowed.
- The run exits **3** when any attempt hit a harness error, distinct from 0 for
  a clean run. `manifest.json` records `integrity.harness_errors`. A controller can
  therefore tell "the models did badly" (exit 0, bad outcomes) from "the harness
  did badly" (exit 3).

## 10. Single vs swarm comparison

`comparison-<task_id>.json` answers: what did the swarm do differently, and at
what cost?

- best single attempt and the explicit, recorded rule used to pick it
- quality difference, **component by component**, never a composite
- cost difference: model calls, model wall time, harness overhead, total wall
  time, tokens
- reviewer contribution: verdict, defect counts, whether a refinement was
  actually issued, whether final validation passed
- controller integration overhead, with its definition stated
- scope limits, stated in the artifact

Scope limits say plainly that one task is not evidence that role separation
beats a single attempt, that no component is aggregated into a single quality
score, and that the artifact records evidence only. A favourable delta is not a
generalisation, and the artifact is built so it cannot be read as one.

## 11. Shipped fixtures

| task_id | family | deliverable | what it guards |
|---|---|---|---|
| `auto_ingest_plan_shorts_live_driver` | `bug_fix` | patch | `planner.plan_shorts(..., driver=driver)` runs after `driver.close()` |
| `auto_ingest_driver_lifetime_regression_test` | `test_generation` | patch | a regression test that actually discriminates |
| `auto_ingest_shorts_plan_review` | `code_review` | analysis | the review surfaces the lifecycle defect and its hazard |
| `auto_ingest_shorts_driver_helper` | `small_refactor` | patch | behaviour-preserving driver-lifetime helper |
| `auto_ingest_plan_shorts_contract` | `contract_reasoning` | analysis | the driver-lifetime contract and what a repair must preserve |

`auto_ingest_plan_shorts_live_driver` is the only fixture with a `broader` tier:
a 14-check suite over the rest of the module — parser surface, the
plans-directory contract, real `Plan` persistence, the brand check, and the
remaining driver-owning handlers — using the real `models` module from the
snapshot. It passes both before and after the canonical repair, so a repair that
fixes the ordering while quietly changing anything else is reported as
`REGRESSION_FAILURE`, not as success.

The campaign fixture is frozen from `scottjoyner/auto-ingest` at
`d7d75fff9e97e6f27056677205a75cbcb49ca48d`. The auto-ingest repository itself is
not modified by anything here.

Why the bug is worth a benchmark: it is silent. `plan_shorts` wraps its content
mining in a blanket `except Exception` that logs at INFO and falls back to
templated text, so `shorts plan <topic>` exits 0 while quietly losing the graph
content the adjacent comment says it is mining. A shallow reading sees "the driver
is closed" and stops.

The acceptance suite fails 5 of 9 checks on the unrepaired snapshot and passes
9/9 on the canonical repair. It asserts the lifecycle contract — graph reads and
planning happen while the driver is open, and the driver is closed exactly once
on every exit path including the `--discusses` early return and any exception
during planning — not merely that `close()` is called somewhere. The obvious
repair that regresses `--discusses` cleanup or leaks the driver is caught.

## 12. Tests

```bash
python3 -m unittest test_fixgit_repro_v1 test_realtask_binding \
    test_realtask_roles test_realtask_patch test_realtask_runner \
    test_realtask_evidence test_realtask_endpoint test_realtask_cli

# or
python3 -m pytest test_realtask_*.py -q
```

| Module | Covers |
|---|---|
| `test_realtask_binding.py` | strict fixture loading, seal/drift detection, hash mismatch, missing file, **wrong HEAD fails closed**, matching HEAD+hashes passes, binding before any model call, tampered source manifest, no bundled secrets, **a nested directory cannot borrow an enclosing repository's HEAD** |
| `test_realtask_roles.py` | the three contracts, tolerant-but-strict parsing, truncated vs protocol failure, verdict exactness, no hidden-reasoning asks, prompt content |
| `test_realtask_patch.py` | extraction strategies, safety screen (undeclared files, traversal, absolute paths, binary, rename), writable prefixes, `INVALID_PATCH` vs `PATCH_DOES_NOT_APPLY`, trailing-newline regression, program allow-list, worktree guard |
| `test_realtask_runner.py` | the taxonomy, grounding gate, every stage, reviewer receives the exact patch and exact binding, **binding drift stops the reviewer**, one-refinement enforcement, review rejection, analysis deliverables, test-generation discrimination, satisfiable-oracle proofs for `small_refactor`, targeted-vs-broader separation, **`REGRESSION_FAILURE`**, **hung acceptance commands are `TIMEOUT`**, **always-emit on harness failure**, multiple single attempts and best-single selection, source-context truncation, `--require-head`, and that the runner never modifies the authoritative fixture |
| `test_realtask_evidence.py` | atomic writes, run layout, manifest provenance, API-key redaction, no hardcoded fleet, comparison components, no composite score, scope limits, provenance separation from legacy artifacts |
| `test_realtask_endpoint.py` | the three endpoint inputs (argv, config file, environment) agree; API keys come from the environment and never reach evidence; no fleet node is named anywhere in the harness or its entrypoint |
| `test_realtask_cli.py` | validate/list/plan-command; each stage runnable standalone (`scout`, `implement`, `review --patch-file`); a multi-task run keeping per-task evidence; exit-code semantics (0 clean, 3 harness error, model failure is neither); and a full run against a loopback OpenAI-compatible endpoint covering SSE parsing, TTFT, usage accounting, `--no-stream`, role ordering, and the comparison artifact |

`test_realtask_cli.py` starts a `ThreadingHTTPServer` on `127.0.0.1:0` purely as
a stand-in for a model runtime someone else started. It binds loopback only and
tears itself down.

## 13. What this harness does not do

No AssistX claiming. No task completion. No auto-router provider registration. No
runtime projection. No production dispatch. No git push to any benchmark target
repository. No server discovery or startup. No shell. No mutation of any
authoritative source tree.

It runs experiments and writes evidence.

## 14. Layout

```
realtask/
    version.py        schema markers, MAX_REFINEMENTS
    taxonomy.py       the eleven outcomes and their precedence
    fixtures.py       RealTask, strict validation, sealing hashes
    binding.py        source binding verification
    roles.py          role contracts, prompts, parsing
    patch.py          extraction, safety screening, syntax
    evaluation.py     disposable worktree, allow-listed execution
    adapter.py        EndpointConfig, OpenAI-compatible adapter, scripted adapter
    metrics.py        component metrics
    evidence.py       atomic writes, run directory, provenance
    compare.py        single-vs-swarm artifact
    runner.py         stage orchestration
    tasks/            frozen fixtures
realtime_bench.py     the canonical CLI
seal_realtask_fixture.py
test_realtask_*.py
docs/HARNESS-REGISTRY.md
EVIDENCE-PROVENANCE.json
```
