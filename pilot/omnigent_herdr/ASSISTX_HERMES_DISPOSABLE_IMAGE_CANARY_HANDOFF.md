# AssistX Hermes — actual image, disposable zero-network containment canary

**Fleet Commander research checkpoint: October 20, 2026**
**Outcome: physical disposable image containment PASS; production Hermes migration HOLD**

## Scope

This step uses the **exact Docker image object ID of the currently
running x1-370 `assistx-hermes-adapter`** and launches an entirely
separate, short-lived **synthetic-only** container. It does **not**
modify, recreate or restart the production Hermes adapter, its
Compose project, network, volumes, SSH credentials, environment,
service registration, schedules, agents or external model/provider
connections. Its image's normal `/entrypoint.sh` and
`python -m assistx.strict_executor_main` are explicitly NOT RUN.

The canary is `assistx_hermes_container_canary.py`. It discovers
the running container's immutable local Docker image object ID
via a fixed read-only inspect command and refuses non-SHA256 IDs.
It then creates a uniquely named `og-hermes-image-canary-*`
container from that exact image ID using these operator-reviewed
static restrictions:

- `--pull=never` (no image pulls, network or registry use).
- `--network=none`, `--read-only`, `--cap-drop=ALL`,
  `--security-opt=no-new-privileges:true`,
  `--pids-limit=32`, `--memory=256m`, `--cpus=0.5`.
- Explicit `--user=<nonroot_operator_uid>:<operator_gid>` rather
  than the production adapter's container root UID.
- Exact `--entrypoint=/bin/sh` running a **fixed, synthetic
  static shell script**; it never invokes the Hermes agent,
  shell task engine, provider, router or other application
  components.
- Zero access to production mounts; **only two synthetic host
  directories** under a disposable operator-owned
  `/home/scott/.cache/og-hermes-containment-XXXXXXXX/`:
  `/research/knowledge` mounted **read-only**, containing
  only a constant synthetic marker, and `/workspace`
  mounted **read/write**, accepting one synthetic output file.
- Only a small isolated `/tmp` tmpfs. No NAS/SSD global
  root, host home, Git parent, Docker socket, SSH keys,
  API secrets, Tailnet/host network, source authority key,
  model weights or production knowledge directory is mounted.
- Before starting, `docker inspect` of the scratch container's
  **filtered** user, entrypoint, image ID, HostConfig and Mounts
  metadata must match the exact restriction and two-bind contract.
  Its `Config.Env` and arbitrary container files are never
  fetched or reported.
- Inside the isolated fixed script, a synthetic knowledge
  marker must read correctly; writes through the RO knowledge
  mount and to `/etc` on the read-only image rootfs must
  fail; effective Linux capabilities must be zero and
  `NoNewPrivs` must be 1; the dedicated synthetic worktree
  **must** allow a tiny output write. Source paths matching
  the production SSD, NAS or host SSH-key destinations
  must be inaccessible in the scratch container.
- Container exit code, bounded fixed stdout and removal are
  verified; a `finally` cleanup forcibly removes **only**
  the newly randomized disposable container by its exact
  unique name. Any failure is `HOLD`, never retry, agent
  dispatch, provider execution or a production rollout grant.

### Result actually observed on x1-370

- Runtime x1 Hermes image object ID:
  `sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307`
  (an image ID, **not** an authenticated source revision).
- Two complete **independent, freshly named** physical
  container canaries passed with that exact running image ID.
  Both verified zero-network/zero-production-mount static
  metadata, a non-root operator UID, read-only root and
  knowledge directories, capability drop/no-new-privileges,
  a writable scratch workspace and post-run cleanup.
- A third standalone physical run was initially blocked by
  a harness-fixture permission mismatch: the container's
  non-root UID could not write to a scratch directory owned by
  another UID. The corrected fixture uses the same non-root
  host operator UID and retains private controlled scratch
  bindings; this was independently revalidated. No production
  change was made to resolve the fixture error.
- Production `assistx-hermes-adapter` was still **running**
  with the exact same image ID after the two canaries.
  Zero `og-hermes-image-canary-*` containers and zero
  `og-hermes-containment-*` directories remained.
- The new test module
  `test_assistx_hermes_container_canary.py` has **18**
  negative/contract tests, checking that flags, image ID,
  non-root UID, mount sources, default entrypoint override,
  forbidden synthetic/production scope, fallback failure and
  all required `HOLD` authorities cannot be silently omitted.
- Baseline pilot `747/747` becomes **765/765 full tests PASS**.

## What this canary does NOT prove

This is a physical **container/image filesystem containment
test against SYNTHETIC DATA**; it is stronger than an offline
Compose render, but it does **not** qualify the real running
production adapter. In particular:

- Normal Hermes agent entrypoint, Python tasks, local state,
  knowledge retrieval, CPU inference, provider/catalog
  authorization, job ingestion/output, notebook/research
  tasks, and agent APIs were **not executed**.
- Real host evidence was **not** passed into the test
  container, by design. Its write-denial is on a **synthetic
  RO knowledge mount**, not on actual production historical
  Watchdog files. Physical production evidence custody
  remains unverified.
- The current running adapter still has **16 live mounts,
  nine writable, and two writable aliases overlapping the
  protected Watchdog evidence root**. Both broad aliases,
  host SSH-key mounts, full SSD/NAS mounts and container
  root execution still need remediation under
  [auto-assist P0 #414](https://github.com/scottjoyner/auto-assist/issues/414).
  No attempt was made to deny its existing legitimate jobs,
  alter its mounts or shut it down.
- The local Docker image object ID is NOT a reproducible,
  independently signed image-to-source-build attestation.
  Shared administrator/root authority and rollback of both
  source databases and their trusted heads remain unresolved.
- No authenticated per-owner AVAILABLE vs LOADED model catalog,
  source-authoritative execution receipt, quota, independent
  revocation/high-water, real network partition/fencing or
  online production admission was established.
- Lenovo remains below its independent 8 GiB available-memory
  prefilter and does not have approved AppArmor sandbox policy.

All output fields explicitly retain
`decision=HOLD`,
`production_deployment_authorized=false`,
`agent_dispatch_authorized=false`,
`model_egress_authorized=false`,
`hermes_workflow_compatibility_verified=false`,
`physical_production_evidence_write_denial_verified=false`,
`provider_requests_forwarded=0`, no refunds and no
source-effect execution grant.

## Operator usage and staged deployment

The source file is published inside the existing draft
agent-harness PR and staged at the x1 operator path:

```text
/home/scott/.local/share/assistx-herdr-readonly-20261020/
  assistx_hermes_container_canary.py
  test_assistx_hermes_container_canary.py
  latest_hermes_container_canary.json
```

Without the explicit physical flag this CLI only returns
a non-authorizing NOT-RUN/HOLD record and does not contact Docker:

```sh
python3 -B /home/scott/.local/share/assistx-herdr-readonly-20261020/assistx_hermes_container_canary.py --json
```

On the **physically approved x1 operator host only**, an
operator may opt in to **one disposable** restricted image test:

```sh
python3 -B /home/scott/.local/share/assistx-herdr-readonly-20261020/assistx_hermes_container_canary.py --physical-disposable-canary --json
```

The physical flag always leaves production deployment **HOLD**.
Do not place this command in a recurring automation; it is a
bounded explicit operator test.

## Minimum next gate for a true restricted Hermes deployment

1. Capture a reviewed, exact approved **single-task worktree +
   narrow RO knowledge + private writable app-state** Compose
   candidate, with **zero** broad SSD/NAS/root-home mounts.
   Confirm that Compose's `!override` layer actually removes
   all inherited production mounts and SSH credentials.
2. Test a disposable **normal Hermes entrypoint** only after
   providing synthetic isolated mock service dependencies and
   rejecting all real provider/model/agent execution. Then
   test legitimate Hermes reads and bounded writes, restart/
   rollback and negative attempts through both old aliases.
3. Independently bind the tested image digest to a pinned
   source revision and reviewed build provenance, with
   separate operator key custody and a staged rollback plan.
4. Prove authenticated per-user task authorization, source
   ownership, terminal effect fencing, model residency and
   revocation before any production network or inference route.
5. Obtain explicit operator change approval for a maintenance
   window; only then consider changing the current Hermes
   deployment. Do not auto-promote a green synthetic image
   shell test to permission to restart production.

**RESEARCH-ONLY CANDIDATE PASS / PROD HERMES MOUNTS STILL HOLD /
DRAFT PR / NO RESTART / NO LIVE AGENT / NO MODEL DISPATCH.**
