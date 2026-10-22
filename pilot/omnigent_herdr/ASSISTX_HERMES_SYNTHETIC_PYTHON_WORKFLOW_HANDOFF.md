# Hermes: pinned-image synthetic Python workflow canary (2026-10-20)

**Status: physical synthetic Python workflow PASS on x1-370; production HOLD.**

## Executed

The new assistx_hermes_mock_workflow_canary.py extends the earlier
shell-only containment exercise without touching the live Compose service.
It discovers the exact image object ID from the running
assistx-hermes-adapter:
sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307

It launches a disposable container using pull=never, network=none,
read-only rootfs, no capabilities, no-new-privileges, non-root UID,
resource limits and only two synthetic mounts. It overrides the image
entrypoint and imports the actual assistx.strict_executor_main and its
adapter from image-baked /app/src. It installs the strict authentication
shim and executes the actual run_loop(once=True) against a mocked HTTP
GET. Self-tasks and fleet discovery are disabled; mocked response has
zero tasks. No actual model requests or real jobs are executed.

The smoke next exercises ensure_model_env and record_task_eval
with a synthetic model, verifies the resulting workspace and JSON
registry under the scratch root, and confirms write denial to its
read-only knowledge mount and the image root filesystem.

## Observed physical result

- PASS: synthetic bearer-authenticated poll, one request.
- PASS: real adapter strict-auth initialization and poll path.
- PASS: synthetic workspace creation and evaluation persistence.
- PASS: synthetic read-only knowledge cannot be written.
- PASS: rootfs read-only, non-root process, no capabilities.
- PASS: image pinned to exact running Docker object ID.
- PASS: zero external network and no production bind mounts.
- PASS: disposable container removed; production unchanged.
- PASS: 10 focused offline negative unit tests.
- HOLD: normal Hermes CLI task execution and real API task completion.
- HOLD: real model inference, source-effect fencing, revocation and
  external independent custody.

The first trial failed closed on a PermissionError from a non-root
probe of an inaccessible /root parent. Correctly treating inaccessible
directories as denied allowed the subsequent physical run to exit zero.

This does NOT test original /entrypoint.sh or a real task and does not
exercise the live /app/src source bind. Image-baked code could differ
from production bind-mounted code. The image object ID is not an
independent source-build attestation.

## Use

Default is non-executing HOLD:

    python3 -B pilot/omnigent_herdr/assistx_hermes_mock_workflow_canary.py --json

Explicit single use disposable physical test on x1-370:

    python3 -B pilot/omnigent_herdr/assistx_hermes_mock_workflow_canary.py --physical-mock-canary --json

Even when the physical test passes, it reports decision=HOLD,
production_deployment_authorized=false,
provider_requests_forwarded=0 and real_agent_tasks_executed=0.

## Next gate

Prepare a reviewed narrow-volume Compose override without broad
SSD/NAS/home mounts, with only allowlisted read-only corpus data,
private writable Hermes state, and a clean re-indexed scoped memory
source. Do not copy the old wide semantic index. Use synthetic mock
services to exercise actual task claim, heartbeat, completion and
restart/rollback; bind deployed source to independently reviewed
image evidence, and qualify real task integrity before promotion.

AssistX P0 issue #414 remains open and independent authority custody
is not established. This milestone never authorizes production edits.
