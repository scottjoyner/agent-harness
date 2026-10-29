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
