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
