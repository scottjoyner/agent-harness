# Hermes restricted Compose — knowledge reads and persisted output

October 21, 2026 — Fleet Commander on x1-370. Research-only draft PR #18. **HOLD.**

## Commands and scope

Dry run (does not touch Docker):

~~~sh
python3 -B pilot/omnigent_herdr/assistx_hermes_knowledge_restart_canary.py --json
~~~

Physical opt-in, synthetic-only, on x1-370 as a non-root operator:

~~~sh
python3 -B pilot/omnigent_herdr/assistx_hermes_knowledge_restart_canary.py --physical-synthetic-knowledge-restart --json
python3 -B -m unittest discover -s pilot/omnigent_herdr -p 'test_*.py' -q
~~~

The runner reuses the existing restricted-profile `hermes_rootless_candidate.compose.yaml`,
rendered with Docker Compose and validated through the strict allowlist.
It builds two deterministic offline rootless image overlays and creates
one disposable, randomized Compose project, no network or published ports.
The candidate has ONLY a synthetic read-only knowledge mount and a
separate synthetic writable workspace; it does not mount any SSD root,
NAS5, SSH keys, production source checkout or production credentials.

Two successive starts of the same Compose-created container run the
normal candidate `/entrypoint.sh` and the image-baked
`assistx.strict_executor_main`. Only model/external HTTP task calls
are suppressed by the explicitly staged test-only `sitecustomize.py`.

In the first run, the *real* adapter `_gather_knowledge_context()`
reads synthetic top-level and nested `.md`/`.txt` inputs from the
read-only mount, **after** the fixture explicitly redirects the read
root to `/research/knowledge`. The adapter's own `record_task_eval()`,
`save_eval()`, `load_eval()` and `ensure_model_env()` persist
synthetic records within the writable workspace. The test writes an
explicitly synthetic output file using normal Python I/O, not a model.

The second fresh process reopens the saved synthetic output and eval
record, verifies the source digest and prior result, writes a second
evaluation record, and confirms the accumulated 2-run history.
It also checks RO knowledge/rootfs write denial, the rootless home,
no `.env` files, no real agent/model/provider execution, and
cleanup of all disposable Docker objects, image tags and scratch data.
The production Hermes container remains on the exact original image
`sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307`.

## Important newly confirmed incompatibility

The running adapter's knowledge path remains **conflated**:

- `_gather_knowledge_context` and self-task artifacts use the module
  `KNOWLEDGE_ROOT`; `_selftask_prompt` and self-task output targets
  presume this same root is writable.
- `ensure_model_env` separately reads `HERMES_KNOWLEDGE_ROOT` from
  the process environment and writes model ENV notes underneath it.
- The current restricted Compose manifest sets
  `HERMES_KNOWLEDGE_ROOT=/workspace/knowledge` for safe writes.
  It therefore does not automatically read an external
  read-only `/research/knowledge` input tree.
- The test deliberately redirected the *module variable only* so it
  could exercise real bounded corpus reads; real self-task output
  would then target the now-read-only input tree and fail or be skipped.
  **This workaround must never be mistaken for a release-ready fix.**

Production remediation requires separate, reviewed `READ_ROOT`
and `WRITE_ROOT` behavior in the adapter, hard denial of symlink
escapes/alias routes, per-path provenance for allowed read-only input,
explicit write destinations for self-task outputs and eval registry,
and real synthetic end-to-end task/restart tests without test-only
adapter variable changes. Distinct owner authorization, fencing,
signed image provenance and review-controlled rollback remain separate.

Existing production mounts are unchanged. AssistX issue #414 remains
P0 HOLD until both broad writable evidence aliases, the NAS5 root and
other broad writable ancestors are replaced or justified and physically
verified. There was no production restart, no real model execution,
no network/provider request, and no access to protected data.
