# Terminal-Bench 4 evidence policy

Sanitized, reproducible **official** task result summaries live here. Raw agent
trajectories, screenshots, task payloads, benchmark oracle/verifier content,
secrets, and large environment files remain outside Git in `.tb4-runs/`.

`r9700-pi-baseline-20261028.json` comes from Harbor 0.25.0, official
`terminal-bench/terminal-bench@4.0.0`, Pi 0.85.1, using the private
R9700-backed endpoint exposing `joyner-toolcall`. The physical model is
MiniCPM5-1B-Q4_K_M.gguf (verified from the running host process command),
not a model named `joyner-toolcall`. This one trial involved an image task
without image input support and is **not** an overall benchmark estimate.

Task verifier reward is 0.0, with 0/1 successes; the diagnostics summarize
model/agent behavior without adjusting the reward. Read an actual local run via
`python -m tb4.report <harbor-job-directory>`.

Never feed these benchmark tasks or privileged verifier content back into model
training or automated prompt improvement; use an independent development set.

`r9700-utility-baseline-20261028.json` and
`r9700-utility-thinkoff-20261028.json` record an exploratory same-task
comparison (MiniCPM5 1B, Pi), **not** independent benchmark tasks. With
thinking enabled, the model made no tool calls. With it disabled, it attempted
seven tool calls, six of which were errors. Both verifiers awarded 0.0.

`r9700-html-managed-20261028.json` is a distinct official HTML/JavaScript
filter task run through the managed authenticated Docker-bridge thinking-off
gateway. It completed with no infrastructure error but **reward 0.0**; do not
count this as success. This confirms gateway lifecycle and streaming in Harbor,
not task-solving reliability.

`r9700-native-tool-probe-20261028.json` records a **synthetic** live tool-call
preflight on the R9700. The test invokes a made-up `tb4_probe` function and
checks its JSON arguments; it is not a Terminal-Bench score. The RX480's
existing text endpoint did not emit a native call on the corresponding probe.

`r9700-guarded-vigenere-20261028.json` and
`r9700-guarded-bun-20261028.json` contain official Harbor verifier rewards
for two additional distinct v4 tasks (`interleaved-vigenere` and
`bun-sourcemap-leak`) run with `tb4.pi_agent:GuardedPi` on the R9700-backed
MiniCPM5 endpoint. Both completed without trial exceptions and received reward
0.0. The Bun trial's tool observations confirm that the extension blocked
six repeated failed commands; the model still failed task verification.
The current EISDIR-memory improvement was added after those canary runs and
is validated through deterministic extension event simulation, not another
claim about changed benchmark success rate.

### Physical-device correction and matched context-window experiment

The two original `r9700-guarded-*` filenames above were created before checking
Vulkan device numbering. On Joyner, the corresponding default `Vulkan0` is
**integrated Ryzen graphics**, *not* the dedicated R9700 (which is `Vulkan1`).
The corrected experimental evidence uses `joyner-igpu-*` filenames.

Four official TB4 v4.0.0 records from `legacy-utility-triage` are saved as
`joyner-igpu-ling-unbounded-triage-20261028.json`,
`deathstar-rx480-ling-unbounded-triage-20261028.json`,
`joyner-igpu-ling-bounded-triage-20261028.json`, and
`deathstar-rx480-ling-bounded-triage-20261028.json`.
The machine-readable matrix `paired-ling-context-results-20261028.json`
retains original official rewards, Pi model-turn events, classified error
kinds, and numeric history-compaction telemetry. The bounded runs reach their
normal 28-turn budget, but all four official verifier rewards remain 0.0.
No benchmark answer data, hidden verifier artifacts, inference credentials,
or Tailscale IP addresses were added to the repository.

### Actual R9700 inference (not default Vulkan0)

`joyner-r9700-lfm3-synthetic8-20261029.json` was copied from the existing
Joyner LFM3 probe receipt selected by its exact UTC timestamp
`2026-10-29T04:03:34.835547+00:00`. The service declares `--device Vulkan1`,
which is the discrete AMD GFX1201 R9700, not the default integrated GPU.
`deathstar-rx480-ling-synthetic8-thinking-on-20261029.json` and
`deathstar-rx480-ling-synthetic8-thinking-off-20261029.json` are independent
bounded public eight-case runs of the RX480 Ling endpoint. Both are strict
string-match synthetic instruction tests, **not** official Terminal-Bench.
`synthetic-inference-matrix-20261029.json` is a normalized comparison without
raw outputs, private cases, credential values, or full system prompts.

### Live bridge canary into Trace Viewer (October 29)

`trace-viewer-live-canary-20261029.json` records **actual** metadata-only
streaming from a 10-turn Harbor/Pi run to the separate private SQLite projection
of the Rust Trace Viewer. It saw 10 requests and 34,348 reported tokens before
the official verifier event arrived, then recorded one verified failure
(reward 0.0). The source trace DB in this isolated test was an empty synthetic
SQLite fixture; no production trace source was changed. No raw task content,
model outputs, tool arguments, credentials or session IDs were committed.

### Publisher receipts and total-token provenance replay (October 29)

`trace-viewer-publisher-receipt-replay-20261029.json` is an aggregate-only
receipt from replaying an **already saved** real RX480 Harbor/Pi job through
the current opt-in private JSONL exporter and Trace Viewer importer. All 11
exported events (10 model turns plus one real official verifier) carried an
owner-local publisher write time. All 10 model-turn totals were native Pi
`totalTokens` rather than calculated fallbacks, totaling 34,348 tokens.
The actual official verifier reward remained 0.0. A second index import added
no events. The observed publication-to-import whole-second age was 0 s in
this isolated same-host replay: **not** a measured GPU request duration or a
production end-to-end latency SLA. The synthetic fixture source was never
modified; no new model inference was run. No raw requests, transcripts,
credentials or trial names are stored in this receipt.

### Producer heartbeat replay (October 29)

`trace-viewer-producer-heartbeat-replay-20261029.json` replays the **saved**
real RX480 Harbor/Pi 10-turn archive into an empty synthetic-source private
index using the new heartbeat-enabled exporter. It contains one `active` and
one `stopped` control-plane record, 10 distinct Pi model requests totaling
34,348 source-reported tokens, and the unchanged official verifier failure
(reward 0.0). No new inference was performed; no original trace archive was
modified. Heartbeats do not create model or session records. A second import
created no duplicates. The receipt contains only aggregate evidence, never
raw prompts, private command lines or user trial names.
