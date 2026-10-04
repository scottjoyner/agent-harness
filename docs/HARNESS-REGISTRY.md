# Harness registry: reconciling the benchmark zoo

This repository accumulated several generations of benchmark and harness
scripts. This note records what each one actually does, which call sites exist
between them, and why `realtime_bench.py` is now the canonical entrypoint for
**real-task** benchmarking.

Nothing here is retired. Every script listed below still exists and still runs
the way it always did. This note exists so that the next person does not have to
re-derive the map, and so that nobody "fixes" the zoo by adding
`fast_bench_v8.py`.

Machine-readable version: [`../EVIDENCE-PROVENANCE.json`](../EVIDENCE-PROVENANCE.json).

---

## 1. The reconciliation decision

**One canonical real-task benchmark entrypoint: `realtime_bench.py`.**

It was chosen because none of the existing scripts can do the job:

| Requirement | Why no existing script qualifies |
|---|---|
| Frozen repository task bound to a source revision | No existing script has any notion of git identity. `fixgit_repro_v1.py:122-134` stamps *its own* revision; nothing binds the *task's* revision. |
| Reproducible fixture independent of a mutable checkout | `benchmarks/baremetal/tasks.json` has no source binding and `BM-019` references a path outside the fixture. |
| Candidate patch produced as text and applied by the harness | No existing script applies a patch. They all execute model-authored shell commands directly in the repo cwd. |
| Role separation as a contract, not a prompt convention | `terminal_bench_harness.py` is the only genuine multi-phase structure, but it is unbounded, writes no artifact, and decides the verdict with an LLM judgement. |
| Atomic, versioned evidence | Only `fixgit_repro_v1.py:77-95` writes atomically; the other twelve write paths are `open(...,'w')` + `json.dump`. |
| Time-to-first-token | Zero SSE implementations existed repo-wide. |

It was *not* chosen by discarding the others. Four capabilities were carried
forward rather than rewritten:

| Carried forward | From |
|---|---|
| atomic write: tempfile → fsync → `os.replace` → fsync dir | `fixgit_repro_v1.py:77-95` |
| deadline discipline, process-group kill | `fixgit_repro_v1.py:24-28, :31-49, :52-75` |
| git revision provenance stamp | `fixgit_repro_v1.py:122-134` |
| always-emit invariant (a crashed run still leaves a result) | `fixgit_repro_v1.py:178-199, :386-421` — implemented in `realtask/runner.py` `_run_guarded` |
| objective verifier overrides model judgement | `run_dual_4.py:12-20, :32` |
| fixture loading convention (`Path(__file__).parent / ...`) | `cpm_tb2_bench.py:199` |
| multi-step loop with feedback and repeat suppression | `cpm_tb2_bench.py:143-191` |
| three-type objective verifier | `baremetal_bench.py:137-164` |
| richest command extractor incl. `reasoning_content` | `baremetal_bench.py:76-123` |
| native tool-call argument handling (str or dict) | `native_toolcall_bench.py:79-83` |
| in/out token accounting | `cpm_tb_agent.py:49-50` |
| `/v1/models` health probe | `router.py:80` |
| scripted-model test technique (patched `urlopen`) | `test_fixgit_repro_v1.py:128-144` |

Three pre-existing behaviours were deliberately **not** carried forward:

- `fast_bench_v7.py:166-173` substitutes a hardcoded correct command when the
  model fails, so `bench_v7_*.json` pass rates are not model-attributable.
- `synergistic_harness.py:223-224` reports token totals that are always zero.
- `dual_model_harness.py:279-280` discards the loop verdict that its own
  docstring advertises.

## 2. What each script actually is

Call sites, verified by reading every file: **no script imports or shells out
to another script in this repo**, except `run_dual_4.py` and
`run_terminal_bench.py` (both import `terminal_bench_harness`) and
`test_fixgit_repro_v1.py` (imports `fixgit_repro_v1` and
`build_k2_reflog_dataset_v2`). Five scripts read
`benchmarks/baremetal/tasks.json` as data.

### Throughput / token measurement

| Script | Notes |
|---|---|
| `benchmark.py` | Only file that reads **both** halves of `usage` *and* both server timing fields (`:98-101`) and aggregates them (`:161-171`). Tasks hardcoded (`:40-66`); no artifacts beyond one JSON. |
| `quick_benchmark.py` | 5-prompt smoke test. Request model is hardcoded to `"test"` (`:16`), so model identity exists only in the filename. Shares the `benchmark_*.json` glob with `benchmark.py`. |

**All tok/s figures in this repo are server-reported.** Nothing computed
`tokens/elapsed` client-side, and TTFT was unmeasurable because nothing streamed.

### Single-shot terminal tasks

| Script | Notes |
|---|---|
| `baremetal_bench.py` | First to load tasks from disk (`:36`); best verifier (`:137-164`, the only one with `command_success`); best extractor (`:76-123`). Endpoint hardcoded to a Tailscale host (`:17-23`), and `VIBETHINKER_URL`/`_MODEL` are dead because `call_model` hardcodes `minicpm5-2b` (`:49`). |
| `toolcall_harness.py` | XML protocol variant. The closing-tag regex at `:82` contains a zero-width character, so that branch is unreachable for well-formed model output. |
| `native_toolcall_bench.py` | OpenAI-native `tools`. Best `--model` CLI of this family (`:227-229`). Single-shot. |
| `fast_bench.py` … `fast_bench_v7.py` | Seven successive single-shot loops, all hardcoding port `1235`, all running `subprocess.run(..., shell=True)` in the **repo cwd**. This is why `test.txt`, `numbers.txt`, `count_lines.py`, `date_script.py` and `bench_test/` are committed. `fast_bench_v7.py` additionally fabricates passing results (see above). |

### Agent loops

| Script | Notes |
|---|---|
| `cpm_tb2_bench.py` | The most evolved pre-existing loop: cwd persistence across steps, `bash -n` syntax precheck, auto-run of created scripts, per-difficulty tool budget, repeat-command suppression, structured per-step traces, artifact pre-clean. Only `--max` and `--tasks`: no endpoint, model, temperature or seed. Module-global CWD makes `run_task` non-reentrant. |
| `terminal_bench_harness.py` | The only genuine plan→execute→verify structure across two models, and the most tool-complete (six tools). Writes **no artifact**; `verify()` is an LLM judgement; the plan's `tool`/`params` are parsed and then ignored (`:301-302`). |
| `dual_model_harness.py` | Cleanest role *typing* (`ModelRole` enum + `ModelEndpoint` dataclass). Never executes anything — `subprocess` is not imported. |
| `synergistic_harness.py` | Only pre-existing script with an interactive REPL and a `/v1/models` probe; only one handling XML *and* native tool calls in a single loop. Its reported token totals are always zero. |
| `simple_dual.py` | The weakest. `extract_bash` has an off-by-one (`+ 22` on a 21-character prefix) that returns a garbage slice on a miss; subprocess output is discarded; no verification at all. |
| `cpm_tb_agent.py` | A `terminal_bench` plugin, not an entrypoint. Imports a package that is not installed here, so the file is not importable. Its `DONE`-termination branch is unreachable. Carries the only cumulative in/out token accounting in the repo. |
| `fixgit_repro_v1.py` | The most rigorously engineered file here, and the only one with tests. Single hardcoded task, staged in its own throwaway git repo, so it is not a real repository task. |

### Orchestration and reporting

| Script | Notes |
|---|---|
| `router.py` | Keyword classifier plus a health probe. **Not the production auto-router** — an unrelated artifact of the same era. Nothing in the repo imports it, and its `both` endpoint is the unparseable string `"urlA + urlB"`. |
| `run_dual_4.py` | Hardcoded 4-task re-run. Its `file_check` pattern — an objective file check ANDed with the harness's own verdict (`:32`) — is the single most valuable idea in the zoo and is carried forward. |
| `run_terminal_bench.py` | Aggregation layer for `terminal_bench_harness`. Hardcoded ports; prints hardcoded SOTA figures as fact (`:110-117`). |

### Fixtures

| Path | Notes |
|---|---|
| `benchmarks/baremetal/tasks.json` | 20 tasks (`BM-001`..`BM-020`), 7 categories, read by five scripts. Verification is *exists + size floor*: no content assertion, so a task passes if the file holds N bytes. No git identity. Not a `RealTask` fixture and not loadable as one. |

## 3. Conclusively dead code

Recorded for completeness. Nothing was removed; these are the findings.

- `baremetal_bench.py:22-23` — `VIBETHINKER_URL` / `VIBETHINKER_MODEL` shadowed
  by a hardcoded `"minicpm5-2b"` at `:49`; the "v2.0" identity in the header was
  never real.
- `baremetal_bench.py:185-186` — `total_tokens` / `total_time` initialised and
  never used; the harness never accumulates token totals.
- `cpm_tb2_bench.py:27` — `SYS = BASH_SYS` is a dead alias.
- `cpm_tb_agent.py:80-89` — `continue` on `cmd is None` makes the
  `and not cmd` termination branch unreachable.
- `dual_model_harness.py:279-280` — `evaluate()` is called and its
  `COMPLETE`/`CONTINUE` verdict discarded.
- `toolcall_harness.py:82, :108, :111` — zero-width character inside a regex
  literal.
- `simple_dual.py:43-45` — off-by-one slice.
- `terminal_bench_harness.py:301-302` — `tool` and `params` assigned, never read.

`simple_dual.py` is the closest to conclusively dead: it is strictly dominated by
`synergistic_harness.py`, has no callers, writes no artifact, and cannot verify
anything. It is retained because deleting historical scripts is not this change's
call to make.

## 4. Rules going forward

1. Real-task benchmarking goes through `realtime_bench.py`. Do not add
   `fast_bench_v8.py`; extend `realtask/`.
2. Raw throughput probing stays with `benchmark.py`; single-shot terminal probing
   stays with `baremetal_bench.py` / `native_toolcall_bench.py`. These answer
   different questions from the real-task harness and are not superseded.
3. New artifacts go in `runs/<run_id>/` with a `realtask.*.v1` schema marker.
   Never rename or re-label an existing legacy artifact.
4. Legacy artifacts stay in the provenance registry with their producing script
   recorded. Do not pool them with `realtask.*.v1` evidence.
5. When a legacy script is genuinely superseded, mark its registry entry
   `superseded` and name the replacement. Do not delete the script or its
   committed artifacts.
