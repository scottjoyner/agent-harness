# AssistX Hermes — rootless startup + standalone Compose qualification

**Fleet Commander checkpoint: October 20, 2026.** Research staging only.
**Decision: HOLD. Never merge/deploy on the strength of this result.**

## Live image-rootless incompatibility, physically reproduced

The *exact* x1-370 running Hermes image was inspected:
sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307.

The image /entrypoint.sh SHA256 exactly matches the
auto-assist-deploy-20261016 checkout's startup script:
49d4006f91e3ddb2e1bfd0f1db3c4a8bb6d08f071e39febf16d2dfc889d8dc70.

A disposable, networkless, read-only, nonroot test with no production
bind mounts attempted the *normal* image ENTRYPOINT. It failed at
"mkdir: cannot create directory /root: Permission denied".
That is a genuine **normal-startup NO-GO** for the original image.
Prior shell and Python canaries bypassed the ENTRYPOINT and could
not qualify rootless startup.

The original ENTRYPOINT also copies the environment to an .env file
for root execution. That approach is not an approved credential
persistence mechanism for a rootless production replacement.

## New research-only candidate

- hermes_rootless_entrypoint_candidate.sh: absolute non-root home/config
  directories, no environment-to-file credential serialization;
  refuses ENV_SEED, refuses root and forbidden /root and /etc targets.
  Initializes only private Hermes state and execs its command.
- hermes_rootless_candidate.compose.yaml: separate synthetic-only
  Compose project/profile with no production Compose inheritance,
  no network, no image pull, rootless UID, immutable rootfs, dropped
  capabilities, no-new-privileges, capped resources and exactly two
  explicit synthetic volume targets.
- assistx_hermes_rootless_compose_guard.py: standalone fail-closed
  real docker-compose-config *render-only* verifier, sanitized process
  environment, random private fixtures, exact mount source and
  permissions audit, never runs Compose up or touches services.
- hermes_synthetic_sitecustomize.py: fixed one-poll synthetic HTTP
  interceptor for a future normal-entrypoint agent Python test. It is
  a TEST FIXTURE, never intended for a real deployment.

## Physical rootless-startup canary (successful)

Created a new disposable local test image by copying only the hardened
entrypoint over the pinned, existing running Hermes image. Since Docker
build rejected bare FROM sha256 as a registry reference, the base image
was assigned a temporary local tag and built without pulling a base.
The positive disposable run used network=none, read-only root, UID/GID
1000, all capabilities dropped, NNP, capped CPU/memory and only the
two synthetic knowledge RO and workspace RW mounts.

The *normal candidate /entrypoint.sh* initialized Hermes config.yaml
under private /workspace/hermes-home, linked the config under
/workspace/.hermes, executed an innocuous shell command and returned
ROOTLESS_ENTRYPOINT_PASS. Synthetic FLEET_ROUTER_BEARER_TOKEN was NOT
persisted in .env. The test image ID was
sha256:b23f4ae54e4c0fb1f5cbf67bac65256ceef3a6f2b532cd903370655a8f1d2cf3.
Temporary base/test tags and scratch mounts were cleaned up.
This is an ephemeral derived image, NOT attested to a published source
commit and NOT a production deployment artifact.

## Negative and render-only results

- Standalone docker compose config JSON render: PASS.
- Exact two fixture mounts and no inherited production services: PASS.
- All 10 new static/negative unit tests: PASS.
- Full pilot suite on x1-370: **797/797 PASS**.
- Original production Hermes remained running from original image.
- The combined normal-entrypoint + mocked Python poll execution did
  NOT run: that attempted physical tool invocation was blocked.
  Do not count the fixture's compile/test as a physical workflow PASS.
- No authenticated real task claims, agent CLI, provider/model calls,
  image-to-source attestation or independent revocation custody obtained.

## Next release gates

1. Review an upstream rootless entrypoint implementation and create a
   reproducible, source-pinned build with signed custody evidence.
2. Execute normal entrypoint and strict_executor_main against a fully
   isolated mock API with no production secrets/volumes/network;
   verify denial, restart, lease loss and bounded task behavior.
3. Approve narrowly scoped Hermes-owned RO knowledge and private RW
   state, rebuild fresh scoped memory index, never copy legacy wide
   index or writable Fleet Watchdog evidence.
4. Verify runtime manifests, true source-effect fencing and offline
   replay/revocation with independent authority; peer review and
   documented rollback before any real replacement.

AssistX issue #414 remains P0 OPEN. Production still has 16 mounts,
9 writable, and 2 writable aliases overlapping protected Watchdog
evidence. No production Compose, volumes, routes or containers changed.
