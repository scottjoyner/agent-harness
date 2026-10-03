# Evidence provenance

Two artifact families live in this repository. They are not interchangeable.

| | Legacy | Current |
|---|---|---|
| Schema markers | none, or a producer-specific string | `realtask.*.v1` |
| Machine-readable registry | [`EVIDENCE-PROVENANCE.json`](EVIDENCE-PROVENANCE.json), `legacy_artifacts` | same file, `current_artifacts` |
| Binds a source revision | no | yes, by SHA-256 and HEAD |
| Records agent-harness git SHA | only `fixgit_repro_v1.py`, and only for itself | every run |
| Written atomically | only `fixgit_repro_v1.py` | every artifact |
| Role-separated attempts | no | yes, per role directory |

## Rules

1. Do not add a `realtask.*.v1` artifact to a legacy artifact pool, or vice
   versa.
2. Do not rename or re-label a legacy artifact with a `realtask` schema marker.
   They were produced under different semantics by scripts that still exist.
3. A qualification claim must cite a `realtask.*.v1` artifact and name the
   fixture SHA it was produced against.
4. When a legacy script is superseded, mark its registry entry `superseded` and
   name the replacement. Do not delete the script or its committed artifacts.

## Legacy artifacts

`bench_*.json`, `bench_v*_*.json`, `benchmark_*.json`, `dual_*.json`,
`cpm_tb2_*.json`, plus `fixgit_repro_v1.py`'s `results.json` / `probe.jsonl` and
`benchmarks/baremetal/tasks.json`.

Each is registered with its producing script, its output glob, the capabilities
that made it useful, and its gaps. Two entries deserve a specific warning:

- **`bench_v7_*.json` is not model-attributable.** `fast_bench_v7.py:166-173`
  substitutes a hardcoded correct command when the model call fails, so the
  pass rate in that file measures the harness's fallback, not the model. Do not
  cite it as model performance.
- **`benchmark_MiniCPM5-LoRA_20260911_123627.json` has an ambiguous producer.**
  Both `benchmark.py` and `quick_benchmark.py` write to the `benchmark_*.json`
  glob.

`test_fixgit_repro_v1.py` also exercises these modules, so removing a legacy
artifact or script will break a test. That is deliberate: the history stays
loadable.

## Current artifacts

Everything under `runs/<run_id>/`. Every file carries a `realtask.*.v1` schema
marker, and `manifest.json` additionally records the agent-harness git SHA and
dirty flag, the fixture SHA, the endpoint identity, host details, argv, options,
timestamps, an integrity check that the authoritative source was unchanged, and
an explicit statement that no production subsystem was mutated.

Layout and field meanings: [`docs/REAL-TASK-BENCHMARK.md`](docs/REAL-TASK-BENCHMARK.md) §9.
