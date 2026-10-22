# Hermes rootless normal-entrypoint startup — reproducible isolated canary

**Fleet Commander physical checkpoint: October 21, 2026 (America/New_York)**
**Disposition: DISPOSABLE SYNTHETIC PASS / RELEASE AND REAL AGENT EXECUTION HOLD**

## Gap resolved

Prior Hermes synthetic canaries executed the actual Python adapter and
task-lifecycle code, but they **overrode the image's entrypoint**. A
separate rootless-entrypoint test invoked only a trivial shell command.
The combined normal `/entrypoint.sh` and
`python3 -B -m assistx.strict_executor_main` startup path had not been
physically proven within the restricted synthetic container.

A later two-build overlay check proved same-session repeatability with
`SOURCE_DATE_EPOCH`, but did not prove **cross-session reproducibility**.

This milestone adds the bounded, opt-in
`assistx_hermes_normal_startup_canary.py` and
`test_assistx_hermes_normal_startup_canary.py`.

## What the physical canary actually does

Only on x1-370, as a nonroot operator:

1. Read the **exact image object digest** from the running
   `assistx-hermes-adapter` without reading its environment,
   credentials, source files or mounted data.
2. Acquire an exclusive, private lock for a **stable temporary
   Docker base tag**. Abort if another lock or pre-existing image
   tag is present. The base tag is bound to the exact running
   image. No registry pull is permitted.
3. Copy the reviewed rootless
   `hermes_rootless_entrypoint_candidate.sh` into a private,
   **synthetic-only** Docker build context; normalize the
   copied entrypoint **and** Dockerfile mtimes to
   `SOURCE_DATE_EPOCH=1704067200`. The checked-in source
   file is never modified.
4. Make two separate `docker build` calls with
   `--network=none --pull=false --no-cache --provenance=false`,
   fixed base tag and fixed source epoch. **Both image object
   digests must match**, not just layer size/tags.
5. Create one scratch-only container from that verified image ID.
   Run the **candidate's normal image entrypoint** (not an
   `--entrypoint` override), with its command set to
   `python3 -B -m assistx.strict_executor_main`.
6. Run UID/GID `1000:1000`, `--network=none`,
   read-only rootfs, `--cap-drop=ALL`,
   `no-new-privileges`, capped memory/CPU/PIDs and
   only two synthetic binds: one RO
   `/research/knowledge` fixture and one dedicated RW
   `/workspace` fixture. Docker's created mount list,
   rootfs, user, capabilities, resource limits, entrypoint and
   command are **inspected before starting**.
7. Supply only the checked-in test-only `sitecustomize.py` as a
   private scratch-file interceptor. This shim replaces all
   `requests.request` calls with **one synthetic localhost GET
   task poll**, rejects subprocess launches and prevents
   self-task/fleet discovery. **No real task, Hermes CLI, model,
   provider, source effect or external network request runs.**
8. Verify the mock poll completed through the normal Python
   startup path, private Hermes config initialized, the
   `verified.marker` was written in private scratch,
   synthetic knowledge and image root writes were denied,
   and no credential `.env` files were created.
9. Recheck the still-running production adapter's original image
   digest/status. Remove **only** the random scratch container,
   two random overlay tags, the exclusively locked test base tag
   and the synthetic scratch directory. Verify Docker no longer
   recognizes those objects before reporting PASS.

**On any missing identity/permission/startup/cleanup check, this
diagnostic stays HOLD.** Even all green results explicitly record
`production_deployment_authorized=false`,
`agent_dispatch_authorized=false`,
`real_agent_tasks_executed=0`,
`provider_requests_forwarded=0`, and no source-owned authority.

## Physical evidence and reproducibility counterexamples

**Pinned running upstream image object:**

`sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307`

**Negative control A:** Two within-session no-cache builds matched,
but independently rerunning the procedure with a randomly named
temporary base image produced different overlay image objects.

**Negative control B:** A **stable** exclusively locked base tag
alone did not fix the cross-session mismatch; each independent build
context still copied the entrypoint with a new filesystem mtime.

**Corrected physical experiment:** Use a single exclusive stable
temporary base tag **and normalize the copied entrypoint and
Dockerfile input mtimes** in addition to setting
`SOURCE_DATE_EPOCH`.

Two independent full executions then returned exactly the same
temporary overlay image object:

`sha256:9d5c1894398c0d62943f6aa5eb2dddc3d5b6d808138b2b02bbd84fdb66c0aa23`

Each independently executed **two** no-cache builds with equal
image IDs, then the normal entrypoint + synthetic one-poll
Python startup passed. No source error, forced privilege or
provider connection occurred.

All temporary overlay/base tags, disposable containers, lock and
scratch directories were removed. The production Hermes container
remained running from the same original upstream image object.

## Tests

- **24** focused unit/negative tests for the new canary,
  including dry-run default, counterfeit admission flags,
  Docker command pinning, impossible image IDs, no entrypoint
  override, forbidden host source paths and symlinks,
  strictly typed zero task/provider counts, random tag injection,
  exclusive stable-base lock, accidental base-tag overwrite refusal,
  independently timestamp-normalized build-context files,
  physical-pass evidence shape and missing-field rejection.
- Existing pilot suite + new tests: **833/833 PASS**.
- Physical operator exercise: **two separate complete
  normal-entrypoint canaries PASS**, each involving two no-cache
  overlay builds. Local image object equal across all four
  corrected builds.
- No production Compose/app settings, real Hermes adapter,
  API worker, sources, NAS, SSD, keys, model providers, network
  routes, ports or services were changed.

These are same-host local Docker reproducibility checks, not
signed provenance from a second builder. Fixing input mtimes
makes this particular narrow entrypoint overlay repeatable;
it does NOT establish complete reproducible builds of upstream
AssistX source or independent release approval.

## Safe usage

Default is dry-run **HOLD**, does not invoke Docker:

```sh
python3 -B pilot/omnigent_herdr/assistx_hermes_normal_startup_canary.py --json
```

Explicit single-use physical research ONLY on x1-370:

```sh
python3 -B pilot/omnigent_herdr/assistx_hermes_normal_startup_canary.py \
  --physical-disposable-normal-startup --json
```

Do not schedule automatic physical canaries or wire their output to a
production agent/router as execution authority.

## Operator-tool staging (no production deployment)

The guard and its four source-only dependencies were copied to a
private ordinary-user staging directory on **both** x1-370 and Beelink:

```text
/home/scott/.local/share/assistx-hermes-normal-startup-20261021/
```

Directories are mode 0700; executable source files, fixtures, tests and
sanitized `latest_normal_startup.json` reports are mode 0600. The
file SHA-256 on each physical node matches the tested checkout:

`2d247bbab9a66822aa03351f83f68aa0a45010ffec1a3573e96f89392ce65117`

**24/24 focused tests pass independently on x1 and Beelink.**
Default `--json` on either host creates only an explicit HOLD report
without starting Docker. The physical opt-in is restricted to **x1-370**
and should not be invoked automatically on Beelink.

Operator command from x1-370:

```sh
python3 -B /home/scott/.local/share/assistx-hermes-normal-startup-20261021/assistx_hermes_normal_startup_canary.py --json
```

To run one operator-approved *synthetic, disposable* qualification,
add `--physical-disposable-normal-startup`; no real agent or model
execution is permitted. The staged tool was not installed as a
background service, agent, model router, container image, or
auto-start task.

## Release HOLD remains

- The actual production Hermes adapter still has **16 mounts,
  nine writable**, including two broad writable aliases of
  the protected Watchdog evidence subtree.
- The tested overlay is deleted after qualification and **not**
  an independently signed, published or deployed production
  replacement. No complete source-provenance chain.
- The **normal rootless startup** is exercised against a monkey-
  patched *empty* task poll, not a remote authenticated task,
  real claim/heartbeat/completion, provider call or true lease
  revocation across a partition. Earlier in-process synthetic
  lifecycle tests remain separate.
- A replacement would need an independently reviewed,
  narrow Compose/data layout, versioned scoped memory rebuild,
  identity- and owner-bound model authorization, real source
  transaction fences and independent revocation high-water.
- Lenovo's separate 15-source SAME-HOST Herdr admission
  remains HOLD: AppArmor approval and sufficient memory,
  model/provider readiness, and independent source authority
  remain unverified.

**Draft PR / no merge / no production cutover / no agent or model
dispatch / no auto retries or refunds.**
