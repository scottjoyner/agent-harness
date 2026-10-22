# AssistX Hermes adapter: live mount-topology HOLD and remediation contract

**Physical Fleet Commander checkpoint: 2026-10-20**
**Decision: READ-ONLY MOUNT GUARD STAGED; CONTAINER REDEPLOY NOT AUTHORIZED**

## Finding and why it matters

AssistX's running `assistx-hermes-adapter` on x1-370 is launched by
the separate Compose checkout at:

```text
/home/scott/git/auto-assist-deploy-20261016/docker-compose.yml
/home/scott/git/auto-assist-deploy-20261016/compose.override.yml
```

Read-only `docker inspect --format '{{json .Mounts}}'` on the
actual running container reports **16 mounts; 9 writable**. At least
two writable host bind mounts can encompass the historical Fleet
Watchdog evidence tree on SSD:

| Source | Inside Hermes container | Docker RW |
| --- | --- | --- |
| `/home/scott/knowledge` (symlink to SSD knowledge) | `/root/knowledge` | true |
| `/media/scott/SSD_4TB` | `/media/scott/SSD_4TB` | true |

These are **two independent writable aliases of the same protected
research evidence** under Docker's mount metadata. It is not a
claim that malicious writes or tampering have actually happened;
no container file access, writes or permission-bypass tests were
performed. It does mean that historical Watchdog evidence is not
eligible to be treated as independently protected solely because
of source-side signing or SQLite replay tests. This condition is
tracked under [auto-assist P0 #414](https://github.com/scottjoyner/auto-assist/issues/414).

## Deployed one-shot mount guard

New file `assistx_hermes_mount_guard.py` reads *only* the live
container's `Mounts` field via a **fixed read-only** Docker inspect
command. It never calls `docker exec`, reads `Config.Env`, uses
`docker compose up`, starts a process inside the container, opens
source/evidence files or changes any mount.

The guard canonicalizes host-side symlinks/paths before checking
whether writable bind **sources overlap or enclose**:

```text
/media/scott/SSD_4TB/knowledge/50-Research/model-evaluation/free-first-monitor
```

Unlike simple string-prefix matching, it treats `/home/scott/knowledge`
and `/media/scott/SSD_4TB` as possible independent entrypoints
into the protected tree. It detects distinct RW ancestors and RW
children, malformed/duplicate/oversized Docker JSON, missing
bind-source paths, unsupported metadata, nonboolean write flags
and conflicting destination mounts. This is observational;
`os.path.realpath` is a path-scope check, **not** a durable
kernel-enforced restriction, and it cannot detect file-descriptor
aliases, arbitrary symlink changes made after inspection, loaded
legacy processes or a separate privilege boundary.

Sanitized evidence outputs contain only aggregate mount counts,
boolean risk flags and blockers — not host mount paths, Docker
environment, credentials or API payloads. **Every result remains
`decision=HOLD`**, with agent and production deployment flags
false and `provider_requests_forwarded=0`.

### Actual staged installations

```text
/home/scott/.local/share/assistx-herdr-readonly-20261020/
  assistx_hermes_mount_guard.py
  test_assistx_hermes_mount_guard.py
  latest_hermes_mount_guard.json
```

Both x1-370 and Beelink have private operator-owned copies,
0600 files and a 0700 staging directory. Run read-only:

```sh
python3 -B /home/scott/.local/share/assistx-herdr-readonly-20261020/assistx_hermes_mount_guard.py --json
```

**Tool SHA-256 on both nodes**:
`7c31b5da3cd32766359b6120d9f8d62b209c670ba82628f79a612fba7bd4390f`.

**Physical x1 result:**
`source=live_docker_inspect`, `total_mounts=16`,
`writable_mounts=9`,
`protected_evidence_rw_aliases=2`,
`mount_topology_static_check_passed=false`,
`decision=HOLD`.

**Physical Beelink result:**
`source=unavailable_docker_inspect`,
`docker_metadata_observed=false`,
`decision=HOLD`. The ordinary operator account cannot
independently inspect its rootless Docker daemon in this run.
No x1 data are misattributed to Beelink.

**Tests**: 20 focused mount-guard tests pass on x1 and on
Beelink separately. The full x1 pilot suite increases from
727 to **747/747 passing**.

## What a reviewed candidate could look like — not yet deployed

The **minimum defensible static mount proposal** for a later
operator-approved isolated Hermes canary is:

- Read-only, independently verified **knowledge subtree**
  (no RW alias under `/home/scott/knowledge` or SSD ancestors
  that can reach protected Watchdog evidence).
- Dedicated, narrowly scoped **writable Hermes app-state
  directory**, outside the protected evidence subtree.
- A single disposable **approved task worktree** as the
  only writable source workspace, with explicit owner/task
  approval. Existing
  `compose.executor-scope.yml` and
  `compose.executor-repository.example.yml` use Compose
  `volumes: !override` for similar bounded test scope.
- No broad SSD-root, NAS-root, global git-root or SSH-key
  mounts inherited from the production adapter. Require a
  separate operator-reviewed recovery/SSH overlay if needed.
- Negative path-access tests in a **disposable container**:
  attempts to create/modify watchdog evidence through
  *both* old aliases and any replacement alias must fail;
  verify required Hermes ingestion/output/rollback functions
  still operate correctly, including degraded/offline behavior.
- Exact candidate image digest/source commit plus verified
  running Docker mount metadata, independent review, expected
  permissions, rollback plan and protected external custody.

The guard's synthetic tests exercise a RO knowledge subtree
plus dedicated RW scratch and correctly report that its *static
mount overlap check* passes. They **do not** produce a
physical-write-denial witness, independently administer a
cryptographic key, or authorize changing production mounts.

The existing Hermes adapter has active jobs and legitimate write
dependencies. **Do not copy this proposal into the running
Compose file, restart Hermes, change its mounts or drop its
credentials without separate validation and operator approval.**

## Deployment/admission status

The staged AssistX operator probe, read-only catalog bridge and
Hermes mount guard are a bounded **diagnostics-only integration**.
x1 AssistX/Auto-Router local health remains separately verified;
the bridge observes eleven advertised router models and
401 anonymous denials from protected AssistX identity/catalog
routes. This says nothing about which models are LOADED,
account-authorized, quota-backed or able to execute safely.

Lenovo's separate Herdr/Omnigent 15-source SAME-HOST admission
is still HOLD (AppArmor isolation approval, <8 GiB available
memory prefilter, source-owned execution/revocation).
Worker/source SQLite transactions remain SAME-ADMIN and do
not supply immutable independent custody. This mount guard
does not close those gates.

**DRAFT / NO MERGE / NO PRODUCTION REDEPLOY /
NO MODEL LOADING OR AGENT DISPATCH / NO RETRY OR REFUND.**
