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
  from `scottjoyner/auto-ingest@d7d75ff`.
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
- **`test_realtask_*.py`** — 177 tests across fixtures/binding, roles, patches,
  the runner, evidence and the CLI (including a full run against a loopback
  OpenAI-compatible endpoint).

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
