# Hermes offline rootless overlay — two-build reproducibility checkpoint

**Date:** October 21, 2026 (America/New_York)
**Host:** x1-370
**Decision:** HOLD — this is a local reproducibility experiment, not signed release provenance.

## Scope

The experiment uses the exact local image object backing the still-running
production Hermes adapter:

`sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307`

A disposable overlay Dockerfile changes only `/entrypoint.sh` to the
nonroot-only script in `pilot/omnigent_herdr/hermes_rootless_entrypoint_candidate.sh`.
The upstream image's existing application and dependency layers are inherited.
No Hermes tasks or provider requests are executed.

Both build attempts use a temporary local base tag pointing to the exact
running image, `--network=none`, `--pull=false`, `--no-cache`, and
`--provenance=false`. All generated tags and scratch files are cleaned up.
No production Compose configuration, container, mounts or volumes are altered.

## Physical experiment A — negative control

Two independent no-cache builds with no fixed source timestamp returned
different image IDs, which is NOT acceptable reproducibility evidence:

- Build A: `sha256:d3ea92557c64d2bece9566dd5d2b78368625d74337cc21c516ce11ceef96db5b`
- Build B: `sha256:be1229b475fbd412fe1eb4f2cbc6f430dd9b858b9843c8e9d8eafd08a82a9270`

The negative test was intentionally retained rather than reporting a PASS.

## Physical experiment B — timestamp-controlled two-build PASS

A second offline pair used the same temporary base tag and identical entrypoint,
but explicitly set both the environment and Docker build arg
`SOURCE_DATE_EPOCH=1704067200` (2024-01-01T00:00:00Z).

- **Build A ID:** `sha256:4c1b81ed030a5a7ec22db12d35c5a22bec2c5599a51237c3678a65da0bd8cf71`
- **Build B ID:** `sha256:4c1b81ed030a5a7ec22db12d35c5a22bec2c5599a51237c3678a65da0bd8cf71`
- **Both Created values:** `2024-01-01T00:00:00Z`
- **Both last filesystem layer digests:** `sha256:b495be7fe038b413e7ce9a4a6c8f8f462314b20848e1bb57f270a0a3d0da6816`
- **Outcome:** exact local image object and final layer match across both independent no-cache builds.

This proves repeatability of this narrowly scoped overlay procedure on this
host with the fixed timestamp. It is not a complete independent rebuild
from GitHub source or a signed provenance statement. The overlay image
was cleaned up after the test and was not deployed.

## Code and cleanup

The previous verified head [`0fe1e8d`](https://github.com/scottjoyner/agent-harness/commit/0fe1e8d370ad1b7c878b025897d1f93f19b42df4)
is hosted-CI green. Existing unit suite remained **809/809 PASS**.

An attempted reusable executable verifier was blocked before completion
and was **not** executed or published. Its incomplete untracked file was
removed; do not count that as a passing automated guard.

Fleet Commander rechecked the current production container: still running
from the original pinned image, with 16 mounts. No temporary overlay image
tags were left behind. PR #18 remains draft; AssistX issue #414 remains P0.

## Outstanding production gates

1. Independent signed provenance for the upstream/base image and exact source tree.
2. Actual corrected normal entrypoint + normal Python agent startup using isolated
   mock service (previous combined attempt blocked; NOT VERIFIED).
3. Reviewed immutable replacement container with only narrow knowledge RO
   and dedicated private Hermes state RW mounts; no inherited broad evidence paths.
4. Owner-bound admission, independently held revocation/replay high-water
   and effect fencing under real task claims.
5. Versioned memory index rebuild, operator-reviewed rollback evidence,
   external review and verified production data-access denial.

**No deployment, merge, source-owner admission, or production cutover authorized.**
