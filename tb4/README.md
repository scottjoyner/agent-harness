# Official Terminal-Bench 4.0 / Pi harness

This is **not** the historical `run_terminal_bench.py` toy evaluator. This runner
executes official `terminal-bench/terminal-bench@4.0.0` tasks with Harbor's supported
`pi` installed-agent adapter, **using official Harbor verifiers**. It records
complete raw jobs separately and never manufactures benchmark scores.

## Requirements

- Python 3.12+, Docker daemon, Harbor 0.25.0, network access to the pinned TB4
  dataset and Pi npm package, and an OpenAI-compatible model endpoint.
- Set `TB4_LOCAL_API_KEY` to your endpoint's key. For a no-auth *private*
  endpoint, use a non-secret placeholder. Never commit real tokens.
- Host needs enough free disk and RAM for task containers. GPU/large tasks
  may require a cloud environment and must be reported as infrastructure-blocked
  rather than scored as model failures.

## Safe canary

```bash
python3 -m venv .venv
.venv/bin/pip install harbor==0.25.0
export TB4_LOCAL_API_KEY=local-no-auth  # private, no-auth endpoint only
.venv/bin/python -m unittest discover -s tests -p 'test_tb4_runner.py' -v
.venv/bin/python -m tb4.runner preflight --harbor .venv/bin/harbor \
  --endpoint http://xwing:1234/v1 --model openai/ornith-1.5-35b-a3b-apex-mtp \
  --job-name tb4-pi-preflight
.venv/bin/python -m tb4.runner run --harbor .venv/bin/harbor \
  --endpoint http://xwing:1234/v1 --model openai/ornith-1.5-35b-a3b-apex-mtp \
  --tasks 1 --concurrency 1 --job-name tb4-pi-ornith-one
```

The model name, endpoint, availability, and installed tools must be checked
anew on each fleet node. The first canary is **not** a full TB4 score.

## Reproducibility and evaluation rules

- Dataset stays pinned to major version 4; Pi CLI to 0.85.1, Harbor to 0.25.0.
- Every unique job has a manifest, CLI log, and nested Harbor job artifacts under
  `.tb4-runs/<job-name>/`. This directory is gitignored, not uploaded publicly.
- Pi receives the versioned `pi-task-prompt.md`. `prompt_sha256` is recorded.
- A task passes only if Harbor's verifier reports a passing reward. A dry run,
  process exit zero, or response text is **not** a benchmark pass.
- Include attempted tasks, run counts, environment errors, timeouts, model
  catalog ID, raw evidence paths, and exact code commit when reporting.
- Compare fixed task sets and attempts, not cherry-picked runs; the Epoch AI
  review of TB4 v4.0.0 flagged numerous verifier defects, so track task IDs.
- Never expose benchmark hidden tests or oracle answers to Pi; don't train on
  evaluation tasks. Tune using separate development tasks instead.

## Analyze actual results

```bash
.venv/bin/python -m tb4.report \
  .tb4-runs/tb4-pi-r9700-real1-20261028/harbor/tb4-pi-r9700-real1-20261028 \
  --output .tb4-runs/tb4-pi-r9700-real1-20261028/diagnostics.json
```

The report reads **official** `verifier_result.rewards.reward` and records Pi
tool-loop errors. Image-modality mismatch is an explanatory flag, **not** a pass
or an excluded task. Benchmark scores are never adjusted using these flags.

## Managed inference gateway for small thinking models

Pi 0.85.1 supports native OpenAI tool calls. Some local thinking-model chat
backends spend their entire response budget in `reasoning_content`, issuing no
tools. On x1-370, this gateway forwards streaming OpenAI chat requests to the
**already-running GPU model** and injects `chat_template_kwargs.enable_thinking=false`.
It preserves every tool specification and passes the official verifier unchanged.

```bash
export TB4_LOCAL_API_KEY=local-no-auth  # only for a no-auth private endpoint
.venv/bin/python -m tb4.runner run --harbor .venv/bin/harbor \
  --endpoint http://100.83.215.83:1234/v1 --model openai/joyner-toolcall \
  --disable-thinking-gateway --gateway-max-output-tokens 2048 \
  --include html-js-filter --job-name tb4-pi-html-managed
```

Gateway binds a dynamically chosen port on the local Docker bridge
`172.17.0.1`, requires the configured bearer token, only serves `/v1/models`
and `/v1/chat/completions`, does not log payloads, and shuts down after Harbor.
For *keyed* upstreams, add `--gateway-forward-auth` only when the same key is
valid for both gateway and upstream. The managed gateway needs the standard
Docker bridge at 172.17.0.1; otherwise use direct mode.

The fixed-task exploratory comparison on `legacy-utility-triage` went from
6,283 output tokens / zero tool calls to 395 output tokens / 7 tool calls;
both runs **failed** (reward 0.0), so this is **not** a resolution-rate gain.
The model repeated malformed shell commands and requires further development.
Use separate, held-out tasks for any claims of benchmark improvement.

## Results so far, October 28, 2026

The first three **distinct** TB4 tasks on the R9700's current MiniCPM5 1B
backend each received reward 0.0: `layout-config-recreation2` (image input
unsupported), `legacy-utility-triage` (no native calls in baseline), and
`html-js-filter` (gateway-enabled but verifier failed). A repeat of
`legacy-utility-triage` with thinking disabled also received reward 0.0 but
produced tool calls instead of reasoning-only output. These four trials cannot
be treated as a representative full-suite score or an unbiased improvement
estimate; they are canary/diagnostic experiments.

## Synthetic capability preflight

Set `--probe-tools` to send a small synthetic diagnostic with a native function
call and include its result in the job manifest. Set `--require-tool-calls` to
reject endpoints that cannot emit a native function call. This **does not**
solve or inspect benchmark tasks and is **not** a verifier pass. It can help
avoid wasting GPU time on an endpoint that only produces prose.

With a no-auth endpoint use both flags safely:

```bash
.venv/bin/python -m tb4.runner preflight --harbor .venv/bin/harbor \
  --endpoint http://100.83.215.83:1234/v1 --model openai/joyner-toolcall \
  --probe-tools --require-tool-calls --job-name tb4-tool-probe
```

## Opt-in Pi extension for invalid command recovery

`--guarded-pi` selects the Harbor 0.25.0-compatible `tb4.pi_agent:GuardedPi`
adapter. It leaves the official verifier untouched. A bundled Pi event hook
blocks an immediately repeated failed tool call, directory reads using Pi's
file reader (including paths observed to yield `EISDIR`), and terminal `cat > file` invocations without content. It does
not guess repairs or rewrite task files. Valid heredocs and `write` are allowed.
The extension is covered by a Node 22 runtime event simulation, as well as
static and Harbor schema tests. The first official guarded canary did not pass;
this is a tool-reliability feature, **not** a demonstrated TB4 score gain.

## R9700 guarded canaries (October 28, 2026)

Two additional distinct official tasks, `interleaved-vigenere` and
`bun-sourcemap-leak`, completed with verifier reward 0.0. The first had 11
tool calls; the latter had 23, including six blocked duplicates in its trace.
Both used the existing R9700-backed model. The tool guard now also tracks
`EISDIR` paths within a trial and prevents repeated file reads of known
directories. That incremental behavior has runtime tests, but has not been
benchmarked as an improvement over the same held-out tasks.

The RX 480 remains a secondary lightweight inference target: its current text
endpoint succeeded on a simple text request but did not satisfy the synthetic
native-function-call gate. Do not route official tool-dependent Pi trials to it
until its model and serving template are validated. `joyner` had very little
free system RAM and full swap at inspection; do not launch new large models
without checking both host RAM and dedicated GPU memory first.

## Bounded-history gateway and device attribution (October 28, 2026)

The default Pi agent may exceed small self-hosted context windows even when
individual native tool calls work. For an endpoint with an **actual** 8,192-token
window, use `--gateway-context-window 8192 --gateway-max-output-tokens 512`
and `--guarded-pi` for diagnostic canaries. The runner creates a fresh random
Bearer token per trial for the Docker-bridge gateway; the upstream endpoint
credential, if needed, remains separate (`--gateway-forward-auth`). Gateway
telemetry records only aggregate counts, never raw prompts, tool output, or keys.

The bounded-context mode conservatively budgets message JSON length and tool
schemas, abbreviates old tool outputs when necessary, then drops complete old
assistant/tool response groups while retaining original system/developer/user
instructions and the most recent tool-call linkage. Even a recent oversized
tool result may be abbreviated rather than rejected. If immutable instructions
cannot fit, the gateway fails closed. This is approximate token budgeting,
not true tokenizer-equivalent context counting; the model can lose old details.
It does not alter the official benchmark task, evaluator, or verifier.

**Physical GPU audit:** the existing `joyner-toolcall` and `joyner-ling`
servers run with llama.cpp's default `Vulkan0`, identified on Joyner as
*Ryzen 5 7600X RADV RAPHAEL_MENDOCINO integrated GPU*. `Vulkan1` is the
*AMD Radeon RADV GFX1201 with 32 GiB*, the actual dedicated R9700. The
existing keyed LFM3 research server explicitly sets `--device Vulkan1`, but
its context is only 2,048 tokens. The paired Ling TB4 tests on Joyner below
therefore used the **integrated GPU, not the R9700**. This corrects the
misleading historical filenames `r9700-guarded-*` in this PR; do not attribute
those earlier trials to dedicated R9700 compute. Deathstar's Ling server
runs the **8 GiB RX 480** with Vulkan GPU layers and observed 100% GPU busy
under the benchmark. Check available **system RAM**, not only GPU VRAM,
before starting a new server: Joyner was under 1 GiB available and swap-full.

A matched run of `legacy-utility-triage` (Harbor TB4 v4.0.0, guarded Pi 0.85.1,
28-turn budget, thinking disabled) compared Joyner's integrated-GPU Ling and
Deathstar's RX480 Ling:

| Endpoint/device | History mode | Model turn events | Failure reason | Official reward |
| --- | --- | ---: | --- | ---: |
| Joyner integrated GPU, Ling | Unbounded | 4 | 8,192-token context overflow | 0.0 |
| Deathstar RX480, Ling | Unbounded | 5 | 8,192-token context overflow | 0.0 |
| Joyner integrated GPU, Ling | Bounded | 29 (28 budget + abort) | Expected turn limit | 0.0 |
| Deathstar RX480, Ling | Bounded | 29 (28 budget + abort) | Expected turn limit | 0.0 |

These results demonstrate that context management prevents *this* specific
premature context overflow, but **not** improved TB4 task success. Source
records and numeric context telemetry are in `tb4/evidence/`. A dedicated
R9700 head-to-head official run remains outstanding; never launch a second
large server under Joyner's observed RAM pressure without a capacity check.

## Explicit dedicated-R9700 inference and RX480 synthetic comparison (Oct 29)

An existing production-safe, isolated LFM3-1.7B-QAD-Q4_0 service on `joyner`
was used for a real eight-case inference test, with `--device Vulkan1` (32 GiB
RADV GFX1201 **dedicated R9700**) and **no new model launch**. The service's
local `online_probe.py` provides fixed public examples and uses its own
protected API configuration; the benchmark collects no auth credentials or
raw prompts/responses from the service. The exact run at 2026-10-29
04:03:34.835547 UTC completed 8/8 HTTP requests, obtained **5/8 strict exact
matches**, median end-to-end **0.2359 s**, and p95 **0.5648 s**. Two responses
had zero-length final content after consuming 64 completion tokens; this
suggests reasoning-budget exhaustion, but it has not been experimentally
confirmed with a thinking-disabled R9700 run.

The live RX480 Ling endpoint on `deathstar` executed the same eight public
prompts via `python -m tb4.fleet_probe` in two modes:

| Endpoint/model | Thinking | Exact matches | Median e2e latency | Completion tokens total |
| --- | --- | ---: | ---: | ---: |
| R9700 / LFM3 1.7B QAD Q4_0 | Default | 5/8 | 0.2359 s | 453 |
| RX480 / Ling 3.0 tiny APEX | Enabled | 8/8 | 0.7611 s | 236 |
| RX480 / Ling 3.0 tiny APEX | Disabled | 8/8 | 0.3431 s | 20 |

The RX480 improved its median by ~55% with reasoning disabled on these fixed
*synthetic* cases while retaining 8/8 exact answers. This is an endpoint/model
and settings comparison, **not an apples-to-apples GPU throughput benchmark**:
models, sizes, prompts' chat templates, and hosts differ. None of these
samples were official TB4 tasks or verifier results. The more relevant TB4
harness regression remains the earlier 0.0 official score on both Ling nodes.

The dedicated R9700 has ~32 GiB nominal VRAM free but `joyner` had only
~0.3 GiB available host RAM with its 4 GiB swap full. Do not infer safe
capacity for launching large additional model processes from VRAM alone.
The RX480 service was measured without stopping or replacing active workloads.

## Optional live Trace Viewer integration

Use `--trace-events-out /absolute/private/path/events.jsonl --trace-host deathstar`
(or the **actual inference machine**) with an ordinary `tb4.runner run` job.
This enables a two-second local-only publisher that extracts completed Pi
assistant *usage* records and final Harbor verifier outcomes into an
owner-private append-only journal. It does not publish prompts, responses,
tool arguments, original task/trial IDs, IP endpoints or credentials.
The companion `scottjoyner/trace-viewer` isolated integration branch
`feature/tb4-telemetry-bridge-20261029` implements `tools/import_harness.py`
for a separate, source-identity-checked analytics index. It can run with
`--watch --interval 5` under explicit operator supervision. The Trace Viewer
Rust dashboard polls every 15s and displays session/token analytics with a
separate official verified-trial pass count.

This is opt-in: without the flags, old Harbor behavior is unchanged. A
completed model turn is not an official benchmark pass, and non-verifiable
trials remain unverified. Public deployment, unprotected live interfaces,
production index mutations, and model changes are not authorized by the
integration. Consult the viewer's `docs/TB4_REALTIME_INTEGRATION.md` for the
privacy, partial-coverage, and journal-rotation rules.

The `Reader.export_to()` path checkpoints log/verifier cursors until the private JSONL append succeeds; a failed/short append is rolled back under a journal lock and retried with deterministic event IDs. The end-to-end field dictionary, unknown-vs-zero semantics and current deployment limitations are documented in Trace Viewer `docs/TB4_TELEMETRY_FIELD_CONTRACT.md`.
