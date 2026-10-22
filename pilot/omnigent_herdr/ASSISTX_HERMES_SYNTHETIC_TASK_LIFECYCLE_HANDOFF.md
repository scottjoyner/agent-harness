# AssistX Hermes — synthetic task lifecycle qualification

**2026-10-20, x1-370. Physical disposable PASS; production HOLD.**

## What changed

The existing hermes image canary previously covered a shell-only test
and then one synthetic strict-authenticated empty poll. The new
assistx_hermes_task_lifecycle_canary.py runs a **full synthetic task
state path** in the image-baked actual Python adapter.

On x1 it uses the exact image object SHA of the running Hermes adapter.
It reuses the existing audited disposable Docker create contract:
network=none, read-only root filesystem, nonroot UID, all capabilities
dropped, no-new-privileges, resource limits, overridden image entrypoint,
and only two synthetic scratch mounts. The mock program is inside the
single disposable RW workspace. No production source checkout, credentials,
NAS/SSD root, Tailscale/network, Docker socket, SSH or knowledge mounts
are exposed. The running Hermes adapter is never restarted.

## Simulated task checks in the actual adapter

1. Missing task token rejects direct run_hermes invocation.
2. One GET to a stub task poll returns exactly one synthetic task.
3. Scoped POST claim obtains a synthetic claim_id using a bootstrap token.
4. Claim-token exchange validates matching task/claim/agent identity.
5. First heartbeat verifies the issued task token and claim identity.
6. Synthetic context lookup, second heartbeat and evaluation persistence.
7. Real adapter process_task control flow calls a **patched fake engine**
   returning a fixed substantive synthetic result. The Hermes CLI itself
   is NEVER launched: subprocess.Popen is intercepted and would fail
   the canary if invoked. This is not a real agent task or model inference.
8. One synthetic memory write and DONE completion through mocked requests.
9. A second synthetic claim loses its lease. Requested DONE completion
   is denied, and the task credential is cleared.
10. Wrong-claim token metadata is rejected. No additional completion
    or fake engine call is permitted.

All HTTP calls are mocked and asserted to address only 127.0.0.1:1.
The container has no network interface to use for provider inference.
Synthetic read-only knowledge and immutable image root remain unwritable.
Scratch model ENV.md and synthetic evaluation registry are successfully
created. Cleanup verifies container removal. No actual tasks run.

## Verified outcome

- Physical disposable lifecycle PASS on x1-370 for the exact running image
  sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307.
- 12 new negative/static contract tests PASS.
- Full pilot unit suite 787/787 PASS.
- No production Compose, services, root filesystem or mounts changed.
- Zero real agent executions, provider requests or external API calls.

The physical result is an in-process mocked contract test of the real
image-baked Python task-lifecycle code, **not** a server-backed signed
claim, provider authorization, true effect fencing, or normal Hermes CLI.

## Operator usage

Default command (no Docker invoked; HOLD):

    python3 -B pilot/omnigent_herdr/assistx_hermes_task_lifecycle_canary.py --json

Explicit one-shot physical disposable test on x1-370 only:

    python3 -B pilot/omnigent_herdr/assistx_hermes_task_lifecycle_canary.py --physical-disposable-lifecycle-canary --json

Output is nonauthorizing even if green:
decision=HOLD, production_deployment_authorized=false,
real_agent_tasks_executed=0, provider_requests_forwarded=0.

## Next release conditions

- Build and review a *standalone* replacement Compose spec using an
  immutable image, explicit narrow RO corpus and private RW state paths,
  no inherited broad SSD/NAS/Git/SSH/home mounts, no automatic start.
- Rebuild scoped semantic memory index; never copy unverified wide index.
- Verify actual normal-entrypoint execution against external isolated
  mock dependencies, with claim/heartbeat/completion, terminal behavior,
  effect fencing, lease loss, restart and rollback.
- Independently bind image to source revision; verify live source bind
  source identity rather than assuming the Docker image has that code.
- Receive independent reviewer approval, source-owner admission and
  independent evidence/revocation custody before traffic cutover.

AssistX P0 #414 is OPEN. The real adapter's live 16 mounts,
nine writable, two overlapping protected evidence remain a blocker.
No production deployment is authorized.
