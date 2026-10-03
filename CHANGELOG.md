# Agent Harness Changelog

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
