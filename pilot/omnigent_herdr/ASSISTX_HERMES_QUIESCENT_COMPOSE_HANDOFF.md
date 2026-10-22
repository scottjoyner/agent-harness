# Hermes quiescent Compose rehearsal (research only)

Date: 2026-10-22. x1-370. Agent Harness draft PR #18.
Decision: HOLD. No production cutover, rollback, merge or agent dispatch.

## Why this exists

Production Hermes runs its original image under Compose with
restart: unless-stopped, 16 binds, nine writable, and two writable
sources that reach protected Watchdog evidence. Restoring that legacy
Compose service after deployment failure is UNSAFE, not valid rollback.

Earlier research showed that a disposable shell-only worker can be
stopped and removed. It did not provide an independently reviewable
Compose definition to prevent accidental agent startup during a
no-dispatch rehearsal.

## New isolated candidate

hermes_quiescent_fallback.compose.yaml is a standalone profile-only
research Compose definition. Its only service hermes-quiescent-synthetic
requires explicit quiescent-canary profile.

Runtime properties: an operator-supplied local disposable image tag,
pull never; /bin/sleep 75 instead of any Hermes entrypoint or Python;
restart=no; network_mode=none; zero ports/mounts/secrets/environment/
env_file/dependencies/build; UID:GID 1000:1000, RO rootfs,
cap_drop=ALL, no-new-privileges and CPU/memory/PID caps.

verify_hermes_quiescent_compose.py validates RENDERED Docker Compose
JSON, not only YAML source strings. It requires an exact single-service
allowlist: no extra keys, exact bounded sleep, zero mounts/network/
secrets, no restart and correct normalized memory limit. Its expected
synthetic image tag must equal the rendered image name. An image tag
is NOT independently verified provenance: the physical run separately
checks the tag's local Docker image ID. A positive policy-only
result still means production_rollback_authorized=false,
independent_claim_revocation_verified=false and decision=HOLD.

## Verification (x1-370)

14/14 pure negative and positive regression tests PASS.
Negative cases reject extra services, Hermes entrypoint/command,
network/ports/extra hosts, devices/bind mounts, env/secrets,
restarting worker, root/privilege bypass, resource cap widening,
unknown image tags and forged extra release fields.

Docker Compose 5.6.0 rendered the candidate and its normalized JSON
passed the strict validator. A UNIQUE disposable Compose project and
temporary local image tag were created. The tag's image ID was checked
against original known local Hermes image
sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307.

Only the synthetic sleep service was started, with --no-build,
--pull never, no dependencies and no other project. Read-only Docker
inspect verified exact Compose project/service, image ID, sleep-only
entrypoint, network=none, read-only root, nonroot, dropped caps,
no-new-privileges, zero mounts, restart=no, no real agent dispatch.
The project was stopped and removed; independent Docker inventory
confirmed zero remaining project containers. The private test marker
survived cleanup.

Observed physical sentinels:
PHYSICAL_COMPOSE_ZERO_MOUNTS_NO_NETWORK_NO_DISPATCH_PASS
PHYSICAL_DISPOSABLE_COMPOSE_QUIESCENT_CLEANUP_PASS

The original production adapter remained running unchanged with
16 mounts and restart unless-stopped. No real NAS/SSD/Watchdog
content, credentials, model/API/provider traffic, task claims or
lease revocation occurred. Disposable project, tag and scratch
resources cleaned.

## Mandatory release HOLD

The sleep stub is NOT a functioning production fallback and does NOT
prove a real no-dispatch transition, in-flight task fencing, external
lease revocation, independent key custody, signed source/image or
rollback-resistant high-water checkpoints.

Production cutover still needs an independently approved immutable
curated knowledge snapshot excluding protected Watchdog evidence,
source-owner task/claim authority, a signed reviewed image/config,
and physically witnessed no-dispatch failback that never restores the
legacy 16-mount configuration. Agent Harness PR #18 and auto-assist
PR #424 remain draft. P0 auto-assist #414 remains OPEN.
