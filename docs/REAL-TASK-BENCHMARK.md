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
specification, and the harness narrows to what an endpoint actually offers.
Rather than
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
and fail transiently. It has been exercised against a real OpenAI-compatible
server (LM Studio) since, where the full profile was accepted with no
degradation and no retries. No fleet node has ever been involved.

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

#### A path inside the worktree is not containment

The safety screen originally screened *path strings*: not absolute, no `..`,
declared by the fixture or under a writable prefix. That is not sufficient,
because a patch can create a **symlink** whose header path satisfies all three
while its target is anywhere on the host:

```
diff --git a/_realtask_tests/test_linked.py b/_realtask_tests/test_linked.py
new file mode 120000
--- /dev/null
+++ b/_realtask_tests/test_linked.py
@@ -0,0 +1 @@
+/home/scott/git/auto-ingest/auto_ingest/shorts/cli.py
```

`git apply` creates the link, and pytest — which imports what it collects —
executes the target's module body at collection time, outside the worktree and
outside the model's read-only guarantee. This was verified end to end before it
was fixed: the linked module's import-time side effect ran.

`screen_patch` now screens declared file modes and admits only `100644` and
`100755`. `120000` (symlink) and `160000` (submodule gitlink) are refused, and
the refusal is reported with the offending mode in octal.

A second defect surfaced while fixing the first: the safety screen's refusal path
never set `safety_ok=False` or `safety_reason`, so the evidence artifact reported
`safety_ok: true` for a patch the screen had just refused — a containment refusal
was indistinguishable from a parse error. Both the outcome and the reason are now
carried through `ApplyResult`.

`test_realtask_containment.py` covers the boundary. One of its tests proves the
linked module really is imported by pytest, because otherwise "nothing executed"
would pass for the wrong reason — it did, until it was replaced with the
property that holds unconditionally: no symlink exists in the worktree after a
run. Four of the five fail with the mode screen neutralised.

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
| `TOOL_CALL_REQUESTED` | the reply called a tool this harness does not provide; it is read-only and passes the bound source in the prompt |
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
usable diff" is the actionable fact. A tool call outranks the truncation it
caused, for the same reason: the diagnosis beats the symptom.

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

One consequence worth stating, because it was a live bug: `--out` defaults to
`<repo>/runs`, so that scratch directory is *inside the harness checkout* — which
the worktree guard rightly treats as read-only. The runner therefore moves the
scratch root to a temporary directory when the requested one falls inside a guarded
tree, and records that on every attempt. The guard itself is unchanged and still
absolute; only the aim moves. Evidence stays where the operator asked for it.

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
- **sample sizes on both sides**, and a scope limit whenever one side is a single
  observation
- **where each candidate came from** (`candidate_source`), and a scope limit when
  it came from an operator rather than a model
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
- Attempts are counted by `candidate_source`, so an operator-supplied candidate is
  never pooled into a model's result
- Artifacts written before provenance existed are bucketed `unknown`, not assumed
  to be model attempts — the missing value could just as easily have been a
  hand-written patch
- A corrupt, foreign-schema or interrupted run lands in `skipped_runs` rather
  than raising: a corpus should never fail to summarise because one run died. A
  manifest whose schema is not `realtask.run_manifest.v1` is skipped, so legacy
  `bench_*` artifacts can never be silently pooled in.
- Runs from **more than one harness revision** are pooled, but the artifact says
  so: `mixed_harness_revisions` is a structured flag and an additive scope limit
  names the revisions and points a reader at the per-run `metrics.json`. Refusing
  across schemas is not enough on its own, because the *meaning* of a component
  can change between two revisions that share one. Found by summarising the first
  real evidence directories, where the totals carried 53.95s of harness overhead
  that was really model latency, recorded before the cost double-count was fixed.
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

| | repositories | distinct source identities | families | families outside the campaign repo |
|---|---|---|---|---|
| campaign | 1 | 1 | 5 | 0 |
| corpus | 3 | 4 | 5 | 1 |

Read that last column before drawing a conclusion from "five families". Bug
diversity and *task-type* diversity are different things, and only the first is
currently broad:

| deliverable | repositories |
|---|---|
| `patch` | auto-assist, auto-ingest, auto-router |
| `analysis` | auto-ingest only |

Every fixture whose deliverable is `analysis` — both `code_review` and
`contract_reasoning` — comes from the campaign repository, as do `small_refactor`
and `test_generation`. So a model that is excellent at writing patches and poor at
reviewing cannot be distinguished by this corpus, and the hardened analysis graders
have only ever been pointed at one defect.

That is a known gap, stated here rather than left for a reader to infer from a
table of families. Closing it means an `analysis` fixture drawn from
`auto-router` or `auto-assist`, which also subjects the graders to a second defect
class. `test_realtask_corpus.py` enforces the disclosure below, so the gap cannot
quietly stop being true while the table still implies it is.

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

### Acceptance must not depend on the host

CI failed 24 tests that passed on every machine I could reach, all of them the
campaign fixture reporting `REGRESSION_FAILURE`. The cause was not the harness:
`cli._brand_check` opens with `from PIL import Image` *before* it looks at
anything, so even the missing-manifest check needs the name to resolve. Pillow is
a real dependency of the upstream project and is not a dependency of this one. It
was installed on the machine that wrote the fixtures and is not installed on a
fresh runner — so the same patch, the same fixture, two different verdicts
depending on the host. An acceptance signal that is a property of the machine is
the one thing this harness exists to prevent.

The fix follows the convention the corpus already had: the auto-router fixtures
stub `pydantic_settings`, so the campaign oracle stubs `PIL` the same way. The
brand check never reaches `Image` on the path under test.

Two tests keep it fixed. `test_the_campaign_broader_tier_passes_without_pillow`
runs the broader tier in a subprocess where a `sitecustomize` meta-path hook
makes `PIL` genuinely unimportable, and asserts 14 passed — the condition CI runs
under. Its companion asserts the blocker actually blocks, so the first cannot pass
because blocking does nothing.

`UndeclaredDependencyTests` generalises it: every non-stdlib import in every
frozen snapshot must either be stubbed by that fixture's oracles or appear in
`UNREACHABLE_DEPENDENCIES` with a specific reason. A bare module name is not a
reason, so a new dependency cannot be waved through. The fixtures that share the
campaign snapshot but only drive the plan path, and the analysis fixtures that
never execute the CLI at all, are listed with why those imports are unreachable
for them.

### An oracle can be racy too

Worth recording because it is the one attack in this document that the harness
aimed at itself. `test_index_score_is_updated_at_in_milliseconds` compared the
index score captured at write time against a *second* `_now_ms()` call, so it
failed whenever the clock ticked over between the two — roughly one CI run in
twenty, on one Python version, reproducing on no machine I could reach. Under an
adversarial clock the old formulation mismatched on 400 of 400 straddling ticks.

It now compares the score against the record's own `updated_at` — two values
captured together, so the clock is never read in the equality — plus a magnitude
bound proving the score is a millisecond epoch rather than seconds or
microseconds, and a one-minute freshness bound with enough slack that a slow
machine cannot flake it.

A benchmark that cannot tell a slow machine from a wrong patch is not measuring
anything, and the only reason this was caught is that the suite now runs somewhere
I do not control.

### The roll-up cannot silently pick the wrong attempt

`build_comparison` names a "best single" so a controller has something concrete
to compare a swarm against. It ranks attempts by a table in
`_select_best_single`, and anything absent from that table falls back to rank 99
— worse than every real outcome. That default is only safe while the table
happens to match the `Outcome` enum, and nothing enforced that: add an outcome
member and it sorts last, the artifact names the wrong attempt as best, and no
other test goes red. The same silent-drift shape as the derived artifacts, in the
one place a wrong answer becomes a headline number.

`test_the_best_single_ranking_covers_every_outcome` asserts the table and the
enum have not diverged, that no outcome is ranked twice, and that ranks are
contiguous from zero so nothing can tie with the fallback. A companion test pins
the orderings that are decisions rather than accidents: `SUCCESS` beats
everything, `TIMEOUT` beats `SOURCE_MISMATCH` (an attempt against a fixture that
would not bind measured nothing, which is worse than a hang), and a harness bug
is worse than a wrong answer.

### What the first live run found

Every check in this document was verified against the harness. One defect was
found only by pointing it at a real model, and it was in the cost accounting.

```python
started = self._overhead_start()
response, failure = self._call(state, Role.SINGLE, ...)   # the model call
self._charge_overhead(state, started)                       # billed as overhead
```

Nine call sites had this shape, so the model's wall time was added to
`harness_overhead_s` *as well as* to `model_wall_s`, and `total_wall_s` came out
at exactly twice the model time. Against the 3B model on LM Studio the first run
reported `overhead/model = 1.00`; the same measurement after the fix is `0.00`.

That corrupted `controller_integration_overhead.harness_owned_seconds` and the
cost difference between single and swarm — the numbers the comparison artifact
exists to produce. A harness cannot answer "is role separation worth its
overhead?" while billing the model's own latency to the overhead.

No test could have caught it. `ScriptedAdapter` reports a constant 1.0s per call,
so every scripted run has had a fabricated model wall time and the arithmetic
error is invisible at that scale. `OverheadAccountingTests` therefore uses an
adapter that reports real elapsed latency, and asserts overhead stays below model
time, that total is not double-counted, and that a three-role swarm does not bill
its three calls twice. All three fail when the fix is reverted.

The lesson generalises past this bug: **a test double that reports plausible but
fixed numbers will hide every defect that lives in those numbers.** Anything the
harness asserts about cost, latency or throughput needs a double that can produce
a range, not a point.

### Two failure modes only a real model produces

The second live run produced something no synthetic test had: a reply that was not
a bad answer but a call to an interface that does not exist here.

```
<tool_call name="read_file" call_id="call_0657...4444...">
```

The harness offers no tools — the bound source is in the prompt — and every role
contract opens with "You have NO tools, NO shell and NO filesystem access". The
model ignored it, then spent its entire 900-token budget padding a `call_id`, so
the attempt was recorded as `TRUNCATED` with the note "contained no JSON object".

Both statements are true, and together they are misleading: an operator reading
that evidence goes to the token budget, when the fix is nowhere near the budget.

`TOOL_CALL_REQUESTED` now names it, and outranks `TRUNCATED` in
`OUTCOME_PRECEDENCE` — the harness orders outcomes by which fact is actionable,
and the diagnosis beats the symptom. Truncation is still recorded in
`outcomes_seen` because it also happened.

Detection is narrow on purpose: three envelope spellings, and a test asserting
that a JSON reply merely *mentioning* tools is not flagged. A classifier loose
enough to fire on the word "tool" would be its own kind of dishonest evidence.

Note what this is not. The harness does not retry, strip the envelope, or offer
the tool. Each of those would paper over a role-contract violation. It records
precisely what happened and lets a reader judge it.

### What the live runs have and have not shown

The harness has been run against a real OpenAI-compatible server (LM Studio, on
loopback) eight times: two fixtures, two models, `single` and one `swarm`. Three
defects were found that no test could have found — the cost double-count, the
tool-call classification, and the cross-revision pooling in the roll-up.

One role call has completed and parsed. On `auto_router_task_contract_lane_mismatch`
the 0.8B model's **scout** finished naturally (`finish_reason: "stop"`, 717
completion tokens), returned valid JSON, and named the seeded defect:

> The `task_contract.py` module incorrectly permits tool execution and validation
> metrics for `refinement`, `repair`, `review`, `repo`, `patch` …

with `relevant_files: ["auto_router/task_contract.py"]` and confidence 0.95. So the
scout stage, the role ordering, the parsing and the evidence have all been exercised
against a real model, and the model was right about the defect.

That run also produced the first `comparison.json` from real data, and the cost fix
is visible in it: `harness_owned_seconds` is `0.000155` against `model_wall_s` of
`33.8`, where before the fix that field carried the model's own latency. The roles
component is correctly marked `comparable: false` with both sides shown.

What has **not** happened is a candidate patch. Neither local model can produce a
unified diff: the 3B model answers with `<tool_call name="bash">`, and the 0.8B model
returns the entire file where a diff belongs, so its response never closes. The
harness specifies the format explicitly — `"a complete unified diff (\"diff --git\"
headers, no commentary)"` — so this is a model capability limit rather than an
under-specified interface, and the honest outcome is `TRUNCATED`. Consequently the
acceptance path has still never been driven by real model output, and no attempt has
ever produced a `SUCCESS`.

The one link never exercised live is a model *writing* the patch. The obvious
hypothesis is that the interface is under-specified — the implementer contract says
`"a complete unified diff"` without showing one. That was tested: a generic,
deliberately trivial worked example of the `diff --git` / `---` / `+++` / `@@`
skeleton was added to the prompt, unrelated to any fixture. It changed the output
shape — the model began emitting `--- a/…`, `+++ b/…`, `@@` — and it still
reproduced the entire file rather than a minimal diff, so the response never closed
and the attempt was `TRUNCATED` again. The change was reverted.

The hypothesis is therefore false: this is a model capability floor, not an
under-specified interface. The contract already names the format exactly, and
showing it does not help a 0.8B model learn to edit. Recorded because a tested and
rejected hypothesis is worth more than an untested one.

So: the pipeline works against a live endpoint, a real model can diagnose a real
defect through it, the acceptance path executes against real evidence, and the
record is truthful about all of it. Nothing has yet demonstrated that a model can
*pass* these fixtures. A reader should treat a non-`SUCCESS` run from a 0.8B model
as evidence about that model, not about the benchmark.

### The second candidate must not inherit the first one's safety verdict

Scripting a swarm end to end — scout, implementer, reviewer saying "revise",
refinement, reviewer accepting — turned up a hand-maintained list that had drifted.
`_merge_review` copies a named set of patch fields from the review attempt into the
swarm attempt, and `safety_ok` and `safety_reason` were not on it. So the swarm kept
the *first* candidate's safety state while every other field correctly described the
final candidate. With the refinement refused by the screen, the evidence read:

```
apply_reason   "patch declares non-regular file mode(s) 120000; only 100644, 100755 ..."
safety_ok      False
safety_reason  ''
```

Evidence contradicting itself about a containment decision is the worst version of
this failure: `safety_reason` is the field a reader or a downstream tool consults
to ask *was this refused by the safety screen, and why*.

The same probe produced a useful negative. The refinement patch **is** screened —
no symlink reached either worktree — and `evaluate()` builds a fresh worktree per
candidate, so a second candidate cannot accumulate on top of a first. Both facts are
now asserted rather than assumed.

`RefinementCeilingTests` covers a ceiling that had no test at all: `--max-refinements`
is documented as able to lower the budget and never raise it, enforced by
`RunnerOptions.clamp` and a second clamp in the CLI. Nothing checked either.

A note on how that one started: `RunnerOptions(max_refinements=99)` returns 99, which
looks like the ceiling being raisable. It isn't — `clamp()` has to be called, and the
runner is what calls it. The bug was in the probe. Worth recording, because the
instinct on finding a defect is to report the anomaly, and the discipline is to
check whether the anomaly is real before writing it down.

### Who produced the candidate

`--scout-file` recorded where a recorded scout came from. `--patch-file` recorded
nothing at all, so an attempt a human solved by hand and an attempt a model solved
were indistinguishable in `metrics.json`, `comparison.json` and the roll-up.

That is not hypothetical. A live `--stage review` run with a hand-written reference
patch scored targeted 1/1 and broader 1/1. Pooled into a roll-up, that is a
benchmark score nobody earned.

`AttemptMetrics.candidate_source` is now `model` or `operator_patch_file`, stamped
the moment the attempt exists and reported in all three artifacts. It sits on the
attempt rather than on `PatchMetric` because `PatchMetric` fields are copied by hand
in `_merge_review` — which is exactly where `safety_ok` and `safety_reason` once
went missing. Attempt-level placement makes it immune to that class of bug.

The manifest records the operator's file path, its SHA-256 and its length. Hashed,
not merely named: a path can be rewritten between runs, so the record has to
identify *which* candidate was judged.

This is the third hand-maintained field list in this harness to drop a field on the
floor — after the best-single ranking table and `_merge_review` — and the roll-up's
`_ATTEMPT_FIELDS` was the one that surfaced it here. The pattern is consistent
enough to be worth naming: **a list of names someone typed is a place for a field to
go missing, and nothing about it looks wrong when it does.**

### One sample is not a measurement

The comparison artifact declared that one *task* is not evidence that role
separation helps. It said nothing about one *sample* — which is the easier mistake,
because a single swarm run against a single single run produces a delta that reads
like a result and is indistinguishable from noise.

It also could not have done otherwise. `--single-attempts` existed; there was no
way to repeat a swarm. So **every** single-vs-swarm comparison this harness
produced was structurally n=1 on the interesting side, and nothing in the artifact
said so. The question the artifact invites — is the difference the strategy, or the
run? — was unanswerable by construction.

`--swarm-attempts` now exists, both sides report their `sample_sizes`, and:

- a single sample on either side is named as a single observation
- the swarm side is always described as one sample, because role separation is
  nondeterministic (scout wording, reviewer verdict), so a per-attempt difference is
  confounded with run-to-run variation — and that is stated as surviving replication,
  not solved by it
- the roll-up records `attempts_per_task` and says when every pooled task
  contributed one attempt, since one run per cell pooled and totalled reads like a
  population

The honest limit: replication makes variance *observable*. It does not make one
task generalisable, and the artifact does not claim it does.

## 12. Tests

Everything here runs in CI on every push and pull request
(`.github/workflows/realtask.yml`), so none of it depends on one machine's
state. Locally:

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
| `test_fixgit_repro_v1.py` | the pre-existing k2 reflog-correction reproduction harness this repo already carried, kept running so the historical evidence stays reproducible; not part of the `realtask.*.v1` family |
| `test_realtask_binding.py` | strict fixture loading, seal/drift detection, hash mismatch, missing file, **wrong HEAD fails closed**, matching HEAD+hashes passes, binding before any model call, tampered source manifest, no bundled secrets, **a nested directory cannot borrow an enclosing repository's HEAD** |
| `test_realtask_corpus.py` | corpus-level guarantees, including proving no acceptance tier depends on an undeclared third-party package, checking that generated-but-tracked artifacts still match their source, and attacking the analysis graders with an adaptively-built keyword dump and the patch oracles with a memorisation patch: every fixture is **satisfiable** (a reference solution reaches SUCCESS), **discriminating** (the untouched snapshot fails), **independent** (no cross-fixture references, no byte-sharing across repositories, no campaign collapse onto one defect), every `patch` deliverable has a tracked reference solution, collateral damage reads as `REGRESSION_FAILURE`, and no credential literal is frozen |
| `test_realtask_roles.py` | the three contracts, tolerant-but-strict parsing, truncated vs protocol failure, verdict exactness, no hidden-reasoning asks, prompt content |
| `test_realtask_containment.py` | the worktree boundary: a symlink patch is refused, the refusal is visible in the evidence, no run leaves a link pointing outward, and the linked module is proven importable so the non-execution check is not vacuous |
| `.github/workflows/realtask.yml` | the same checks on a clean checkout, on Python 3.11 and 3.12: fixture validation, a per-fixture seal check, the full suite, and a guard that the run did not modify tracked files. No secrets; the endpoint variables are blanked so a test that starts depending on a live endpoint fails rather than silently succeeding |
| `test_realtask_patch.py` | extraction strategies, safety screen (undeclared files, traversal, absolute paths, binary, rename, symlink and gitlink file modes), writable prefixes, `INVALID_PATCH` vs `PATCH_DOES_NOT_APPLY`, trailing-newline regression, program allow-list, worktree guard |
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
    taxonomy.py       the outcomes and their precedence
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
