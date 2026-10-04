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

### What a real runtime may not implement

A private OpenAI-compatible runtime need not implement every part of the
specification, and the harness has never been run against one. Rather than
probing -- which would edge into discovering runtimes the operator never
mentioned -- it starts from the full request and narrows only when the endpoint
actively refuses it:

```
full request
  ├─ 400/422 names stream_options → drop it   (token usage may be absent)
  ├─ 400/422 names seed           → drop it   (determinism rests on temperature)
  └─ 400/422 names stream         → go blocking (TTFT becomes null)
```

The ladder stops at streaming on purpose: dropping `temperature` or
`max_tokens` would change what the experiment measures rather than how it is
measured. Separately, timeouts, connection resets and 5xx are retried up to
`max_retries` (default 1). Other 4xx are never retried -- a rejected request is a
configuration problem and retrying only burns the deadline. Degradation does not
consume the retry budget.

Every call records `request_profile` -- which parameters were really sent, plus
the reason for each degradation -- and `retries`. A degraded request that
succeeds is *not* silently equivalent to the request we wanted, and a run that
had to degrade prints `endpoint note:` lines and carries the same detail in
`metrics.json`.

This path is tested against loopback handlers that reject specific parameters
and fail transiently. It has **not** been exercised against real hardware;
proving it needs a node.

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

Role separation can be inspected between invocations. `--scout-file` hands a
recorded scout result to the implementer, so a scout run can be read, judged and
then reused without re-asking the model:

```bash
# read the scout first
python3 realtime_bench.py run --task <id> --stage scout --out ./runs --run-id step1
# then implement, informed by it
python3 realtime_bench.py run --task <id> --stage implement \
  --scout-file ./runs/step1/scout/result.json --out ./runs --run-id step2
```

The handoff is validated as evidence: wrong schema, a missing `scout` block, a
malformed block or invalid JSON are all errors rather than a silently ignored
flag. `manifest.json` records where the scout came from -- its source attempt id,
strategy, outcome, plan length and confidence. Passing `--scout-file` to a run
that uses only `single` warns instead of pretending to have used it.

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
    rollup.json                    cross-task roll-up, written by `summarize`
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

## 10a. Rolling up a campaign

One run produces one `comparison.json`. A campaign produces many, across tasks
and endpoints, and something has to fold them together:

```bash
python3 realtime_bench.py summarize --out ./runs          # table
python3 realtime_bench.py summarize --out ./runs --json   # machine-readable
```

This writes `rollup.json` (`realtask.rollup.v1`) and reports outcome
histogram, per-family and per-strategy breakdowns, per-attempt component metrics
retained, totals for calls/wall/tokens, and which harness git shas and model
runtimes produced it. Integrity failures and harness errors are surfaced, not
buried. It needs no endpoint.

Same stance as `comparison.json`:

- `composite_score` is `null`, always. A test greps the serialized artifact to
  make sure nothing resembling a verdict cannot appear.
- Token totals are `null` when any contributing attempt's endpoint omitted usage.
  A partial sum is worse than none. Call counts stay exact.
- A corrupt, foreign-schema or interrupted run lands in `skipped_runs` rather
  than raising: a corpus should never fail to summarise because one run died. A
  manifest whose schema is not `realtask.run_manifest.v1` is skipped, so legacy
  `bench_*` artifacts can never be silently pooled in.
- Exits `1` when the runs root holds no evidence at all -- "nothing to report" is
  a configuration mistake, not a result.

## 11. Shipped fixtures

| task_id | family | deliverable | what it guards |
|---|---|---|---|
| `auto_ingest_plan_shorts_live_driver` | `bug_fix` | patch | `planner.plan_shorts(..., driver=driver)` runs after `driver.close()` |
| `auto_ingest_driver_lifetime_regression_test` | `test_generation` | patch | a regression test that actually discriminates |
| `auto_ingest_shorts_plan_review` | `code_review` | analysis | the review surfaces the lifecycle defect and its hazard |
| `auto_ingest_shorts_driver_helper` | `small_refactor` | patch | behaviour-preserving driver-lifetime helper |
| `auto_ingest_plan_shorts_contract` | `contract_reasoning` | analysis | the driver-lifetime contract and what a repair must preserve |
| `auto_router_task_contract_lane_mismatch` | `bug_fix` | patch | a plan lane whose tools were never registered, so it can never be routed |
| `auto_router_settings_latency_cache_path` | `bug_fix` | patch | the latency cache path derived by a second, divergent copy of the SQLite grammar |
| `assistx_answers_store_cursor_drops_ties` | `bug_fix` | patch | a keyset cursor that silently drops every answer sharing a millisecond |

### Corpus shape

Five fixtures are one campaign: the same frozen `auto_ingest/shorts/cli.py`,
the same defect, five independent angles on it. That is deliberate — it isolates
*what the harness measures* — but on its own it would not support any claim about
generalisation, so the corpus also carries defects from two further repositories
that share no code, no author, and no bug class with the campaign:

| | repositories | distinct source identities | families |
|---|---|---|---|
| campaign | 1 | 1 | 5 |
| corpus | 3 | 4 | 5 |

`test_realtask_corpus.py` enforces that shape rather than assuming it: it fails
if the corpus collapses onto a single source identity, if two fixtures sharing a
snapshot describe the same problem or the same family, if any two fixtures from
different repositories are byte-identical, if a fixture names another fixture, or
if a credential-shaped default is filled in.

### Campaign fixture: auto-ingest driver lifetime

`auto_ingest_plan_shorts_live_driver` is the only campaign fixture with a
`broader` tier:
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

### Cross-repository fixtures

The three defects outside the campaign share no code with it and no bug class
with each other. Each was found by reading frozen source, and each is the kind of
bug that a single-wrong-line reading walks straight past.

**`auto_router_task_contract_lane_mismatch`** — frozen from `scottjoyner/auto-router`
at `7575d2b1400a5d799c9710927366cf1523ad4cd3`. `task_contract.py` derives
`requires_tools` from a set that enumerates four tool kinds while the plan and
metrics vocabularies enumerate ten, so `implementation`, `refinement`, `repair`,
`review`, `repo`, `patch`, `documentation`, `docs`, `terminal` and `shell` all
produce a lane that can never be routed. The fix is not a missing item but the
realisation that the vocabularies were hand-maintained in three places and should
have one home; the reference repair introduces `_CODE_KINDS`, `_RESEARCH_KINDS`,
`_OPERATIONS_KINDS` and `_PLAN_EXPLICIT_KINDS` and derives the others. Targeted
tier: 29 failed / 13 passed before, 42 passed after. Broader: 27 passed either
way.

**`auto_router_settings_latency_cache_path`** — same repository. `settings.py`
derives the latency cache path by re-implementing the SQLite URL grammar inline,
which agrees with `validate_database_placement` for absolute paths and for
`sqlite:////x`, and diverges for the bare relative form: `sqlite:///data/x`
resolves against the process working directory in one place and is rejected in the
other. The reference repair factors out `_resolve_sqlite_path` and makes both
call sites use it, so the two can never drift again. Targeted: 2 failed / 11
passed before, 13 passed after. Broader: 20 passed either way.

**`assistx_answers_store_cursor_drops_ties`** — frozen from `scottjoyner/auto-assist`
at `e872ed64531d308af9a0e02ad3c813ee0671a7bf`. `answers_store.py` serves
newest-first pages from a Redis sorted set scored by `updated_at` in integer
milliseconds. The score is not unique, but the pagination loop uses only the
score half of the documented composite `'<score>:<id>'` cursor and advances an
*exclusive* bound, so when a page boundary lands inside a group of tied
milliseconds every remaining member of that group is skipped — and because the
cursor can never re-enter a group it already half-crossed, those answers are lost
permanently. Nothing is missing from the store, so the loss survives restarts and
retries; how much is lost depends on page size and timing rather than on the data.
The reference repair makes the bound inclusive and resumes from the last member
actually *examined* rather than the last one *fetched*, which is the subtler trap:
a page that stops early would otherwise skip the remainder of its own window.
Targeted: 8 failed / 8 passed before, 16 passed after. Broader: 30 passed either
way.

### Analysis graders are attacked, not trusted

An `analysis` deliverable has no patch, so its acceptance is a script that reads
the captured answer. A script that scans the whole answer for trigger
substrings measures almost nothing, and that is exactly what the first version
of both graders did: a 391-byte word salad containing every trigger and no
explanation at all passed every finding in `code_review`.

Each finding now has to be carried by a **single proposition** — the answer is
split on sentence and clause punctuation, but deliberately *not* on every period,
so `cli.py`, `planner.plan_shorts` and `auto_ingest_config.get_x` survive intact
— and that proposition has to contain at least three words the grader is *not*
keyed on. "Trigger vocabulary" means the words inside every needle, so an answer
assembled from the pieces of `after driver.close` is padding even though none of
those words is a needle by itself. Answers are also required to make at least two
propositions, and the grader prints why it failed.

`test_realtask_corpus.py` builds the attack from each grader's own
`REQUIRED_FINDINGS` table rather than hard-coding it, so adding a needle cannot
quietly make the test vacuous, and it asserts three things per fixture: the
reference answer reaches `SUCCESS` through the runner, the keyword dump does not,
and neither does the same answer with one trigger per sentence.

### Can the oracle tell a fix from a lookup table?

An oracle that only ever mentions one input can be satisfied by special-casing
that input. This is not hypothetical: a patch that adds

```python
if db == "sqlite:///data/router.sqlite3":
    return "/data/latency_ema.json"
```

to `latency_cache_path` — and changes nothing else — passed **13/13 targeted and
20/20 broader** on `auto_router_settings_latency_cache_path`. Every URL in the
fixture shared the basename `router.sqlite3`, so the memorised literal was
enough. The SQLite grammar stayed exactly as divergent as it was for every
filename the fixture did not name.

The fix is not a stricter assertion, it is more inputs. The oracle now also
sweeps `GENERALITY_URLS`: five filenames and mount points that appear nowhere
else in the fixture, including a hyphen-and-digits name and a nested path. The
memorisation patch now fails 5 of them, the frozen snapshot fails 7, and the
reference repair passes all 18.

`test_realtask_corpus.py` keeps the attack as
`test_realtask_overfit_settings.diff` and asserts it is rejected, that it
applies cleanly (so the rejection is not just a broken diff), and that it is
never filed as the reference solution.

The same probe run against the other fixtures found nothing, which is worth
recording rather than assuming:

| fixture | attack | result |
|---|---|---|
| `auto_router_settings_latency_cache_path` | special-case the one literal URL | **passed before this change** — now rejected |
| `assistx_answers_store_cursor_drops_ties` | widen the fetch window to defeat the boundary | rejected — a huge first page still leaves the exclusive bound, so ties are still lost |
| `assistx_answers_store_cursor_drops_ties` | behave correctly only for `limit >= 100` | inert — one page already covers the index at that size |
| `auto_router_task_contract_lane_mismatch` | hardcode the fourteen kinds | not an overfit — that list *is* the module's own three plan vocabularies |
| `auto_ingest_plan_shorts_live_driver` | reorder only when `PYTEST_CURRENT_TEST` is set | rejected on its own terms |
| `auto_ingest_plan_shorts_live_driver` | reorder only for the oracle's `FakeDriver` double | **passed before this change** — now rejected |
| grounding gate | name every bound path and claim engagement | not a hole — the gate is a floor on engagement, not a measure of understanding |
| reviewer stage | emit many defects to look thorough | not a hole — `bugs_caught_by_reviewer` reports counts beside `final_targeted_passed` and there is no composite score |

The second campaign row is the same failure as the settings one, wearing a
different hat. A patch containing

```python
if type(driver).__name__ == "FakeDriver":
    plan = planner.plan_shorts(..., driver=driver)   # correct ordering
    ...
    return 0
```

passes every behavioural assertion in `test_plan_driver_lifetime.py` and leaves
the real defect completely intact, because for that stub the behaviour really is
correct. An oracle built on a test double cannot tell you a double from a driver
by its behaviour; it can only refuse to be identifiable. The double is now
instantiated under three class names — `FakeDriver`, `_GraphDriverStandIn`,
`Session` — so a repair has to hold for every name rather than one remembered
from reading the oracle. That attack now fails 10 checks; the frozen snapshot
fails 15 and the reference repair passes all 27.

The last two rows are recorded because a negative result is still a result: the
grounding gate is documented as a minimum-engagement floor that deliberately
delegates correctness to the acceptance tests, and the comparison artifact has no
composite quality score for a reviewer to inflate.

### Derived artifacts cannot drift

Some files here are *generated* from something else and then committed, so a
reviewer can read them in a diff. Three of them are duplicated content:

| artifact | generated from |
|---|---|
| `test_realtask_reference_live_driver.diff` | the inline `REFERENCE_REPAIR` in `test_realtask_support.py` |
| `test_realtask_reference_regression_test.diff` | a copy of the campaign oracle `test_plan_driver_lifetime.py` |
| the hardening block in both `check_answer.py` graders | itself — the block must match across the two files |

All three were consistent only because they were regenerated in the same commit
that changed their source. Nothing enforced it, and the failure mode is not a
clean error: a desynced test_generation reference quietly stops discriminating,
or one grader drifts and becomes passable by keyword stuffing again while every
other test stays green.

`DerivedArtifactTests` regenerates each one and compares, and the failure message
names both the artifact and its source. Both drift modes are verified to fail:
weakening `MIN_SUBSTANTIVE_TOKENS` in one grader, and editing the campaign oracle
without regenerating its reference.

## 12. Tests

```bash
python3 -m unittest test_fixgit_repro_v1 test_realtask_binding \
    test_realtask_roles test_realtask_patch test_realtask_runner \
    test_realtask_evidence test_realtask_endpoint test_realtask_resilience \
    test_realtask_rollup test_realtask_cli test_realtask_corpus

# or
python3 -m pytest test_realtask_*.py -q
```

| Module | Covers |
|---|---|
| `test_realtask_binding.py` | strict fixture loading, seal/drift detection, hash mismatch, missing file, **wrong HEAD fails closed**, matching HEAD+hashes passes, binding before any model call, tampered source manifest, no bundled secrets, **a nested directory cannot borrow an enclosing repository's HEAD** |
| `test_realtask_corpus.py` | corpus-level guarantees, including checking that generated-but-tracked artifacts still match their source, and attacking the analysis graders with an adaptively-built keyword dump and the patch oracles with a memorisation patch: every fixture is **satisfiable** (a reference solution reaches SUCCESS), **discriminating** (the untouched snapshot fails), **independent** (no cross-fixture references, no byte-sharing across repositories, no campaign collapse onto one defect), every `patch` deliverable has a tracked reference solution, collateral damage reads as `REGRESSION_FAILURE`, and no credential literal is frozen |
| `test_realtask_roles.py` | the three contracts, tolerant-but-strict parsing, truncated vs protocol failure, verdict exactness, no hidden-reasoning asks, prompt content |
| `test_realtask_patch.py` | extraction strategies, safety screen (undeclared files, traversal, absolute paths, binary, rename), writable prefixes, `INVALID_PATCH` vs `PATCH_DOES_NOT_APPLY`, trailing-newline regression, program allow-list, worktree guard |
| `test_realtask_runner.py` | the taxonomy, grounding gate, every stage, reviewer receives the exact patch and exact binding, **binding drift stops the reviewer**, one-refinement enforcement, review rejection, analysis deliverables, test-generation discrimination, satisfiable-oracle proofs for `small_refactor`, targeted-vs-broader separation, **`REGRESSION_FAILURE`**, **hung acceptance commands are `TIMEOUT`**, **always-emit on harness failure**, multiple single attempts and best-single selection, source-context truncation, `--require-head`, and that the runner never modifies the authoritative fixture |
| `test_realtask_evidence.py` | atomic writes, run layout, manifest provenance, API-key redaction, no hardcoded fleet, comparison components, no composite score, scope limits, provenance separation from legacy artifacts |
| `test_realtask_endpoint.py` | the three endpoint inputs (argv, config file, environment) agree; API keys come from the environment and never reach evidence; no fleet node is named anywhere in the harness or its entrypoint |
| `test_realtask_resilience.py` | the degradation ladder and its order, profile caching, the bounded retry budget, that degradation does not spend retries, that unrecognised rejections stay terminal, and that unreachable endpoints are reported clearly |
| `test_realtask_rollup.py` | single and nested multi-task runs, per-family breakdown, null tokens when usage is missing, no composite score or verdict language, and that corrupt/foreign/interrupted runs are skipped rather than fatal |
| `test_realtask_cli.py` | validate/list/plan-command/summarize; each stage runnable standalone; the `--scout-file` handoff and that the scout really reaches the implementer prompt; a multi-task run keeping per-task evidence; exit-code semantics (0 clean, 3 harness error, model failure is neither); and a full run against a loopback OpenAI-compatible endpoint covering SSE parsing, TTFT, usage accounting, `--no-stream`, role ordering, and the comparison artifact |

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
    summarize.py      cross-task roll-up
    runner.py         stage orchestration
    tasks/            frozen fixtures
realtime_bench.py     the canonical CLI
seal_realtask_fixture.py
test_realtask_*.py
docs/HARNESS-REGISTRY.md
EVIDENCE-PROVENANCE.json
```
