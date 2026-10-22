# Hermes safe rollback: legacy restore is NOT safe

Research checkpoint: October 21, 2026 — x1-370.
Decision: HOLD / PR DRAFT / NO LIVE CONFIG, CONTAINER OR MOUNT CHANGES.

## Production finding

The running assistx-hermes-adapter uses pinned image
sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307
with 16 mounts and nine writable. Two host bind sources canonically
overlap the protected Watchdog evidence tree.

The active Compose definition builds from source, configures
restart: unless-stopped and declares 16 volume entries, but does not
declare a read-only root filesystem or cap-drop. Restoring this
service/mount layout would RESTORE the P0 exposure, not safely roll
back. A future release must have a separately vetted no-dispatch
fallback with independent lease/task fencing.

## New deny-only research verifier

verify_hermes_quiescent_rollback.py defaults to HOLD without touching
Docker, Compose, host filesystem, secrets or the network. Its explicit
read-only/nonroot x1 audit reads Docker metadata and Compose source
and emits only counts, risk categories and a HOLD decision, never
Docker Config.Env or bind source paths.

The pure synthetic_quiescence contract requires a disposable worker
absent after stop/remove, restart policy no, zero synthetic workers,
claims and provider calls, no attempt to restore legacy image/mounts,
and an independent synthetic workspace marker surviving removal.
A positive synthetic check never authorizes production rollback,
source-owner claims, lease revocation, effects or deployment.

## Verified research evidence

12/12 dedicated negative/offline tests PASS, covering unsafe
legacy image/mount restore, auto-restart, in-flight claims, bad
counters, incomplete evidence, protected ancestor/symlink metadata
and forged quiescence. The read-only live audit returns:
live_mount_count=16, live_writable_mounts=9,
protected_overlap_count=2, safe_legacy_fallback=false, decision=HOLD.

Physical x1-370: started only a disposable shell/sleep container
from the original pinned image, overriding the original entrypoint.
It had restart=no, network=none, UID/GID1000, RO rootfs, dropped
capabilities, no-new-privileges, resource caps and ZERO mounts.
Verified it ran, then stopped and removed it, verified absence,
and retained a harmless private synthetic sentinel outside the
container. PHYSICAL_DISPOSABLE_NO_DISPATCH_FALLBACK_PASS.
A first attempt failed only a synthetic newline assertion after
container removal; corrected fixture reran successfully.

No real Hermes task, model, provider, API, NAS, Watchdog data,
production service, mount, config, or real lease was touched.
All disposable container/scratch resources removed.

## Remaining production release gates

1. Never restore the existing unsafe 16-mount legacy Compose service.
2. Independently signed/custodied immutable knowledge snapshot
   excluding protected evidence in BOTH RO and RW paths.
3. Independent lease/fencing/revocation and in-flight effects
   proven across crash/restart/partition before quiescence.
4. Signed reviewed source/image/config digest, a real no-dispatch
   production transition, and independently observed rollback.
5. Separate operator signoff. Keep auto-assist PR #424 draft,
   P0 #414 open, and decision HOLD.
