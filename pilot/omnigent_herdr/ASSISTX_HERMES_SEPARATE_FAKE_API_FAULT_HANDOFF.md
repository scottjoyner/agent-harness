# Hermes restricted candidate — separate synthetic API crash/restart

Date: 2026-10-21. Repo: scottjoyner/agent-harness, draft PR #18.
System: x1-370. Research only. Production Hermes remains unchanged.

## Exact operator commands

Dry run, no Docker:
```sh
python3 pilot/omnigent_herdr/assistx_hermes_fault_process_canary.py --json
```

Opt-in isolated physical qualification, only on x1-370:
```sh
python3 pilot/omnigent_herdr/assistx_hermes_fault_process_canary.py --physical-disposable-fault --json
python3 -m unittest discover -s pilot/omnigent_herdr -p 'test_*.py'
```

## Verified physical scope

- Exact image still backing running production container:
  sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307.
- Two reproducible no-pull overlays use image:
  sha256:9d5c1894398c0d62943f6aa5eb2dddc3d5b6d808138b2b02bbd84fdb66c0aa23.
- No Docker Compose operation or production container restart.
- Normal candidate rootless entrypoint boots real image-baked strict executor
  module; Docker network=none, UID 1000, read-only rootfs and synthetic
  knowledge, dropped capabilities, no production mounts.
- Unlike the prior thread-based fixture, the fake AssistX HTTP server runs
  in a separate Python OS process, with explicit SIGKILL and restart.
- It runs three times during one container startup, with only localhost HTTP.
  Its clock, task journal and tokens are wholly **synthetic**.
- Real strict executor client makes HTTP claim/token/heartbeat and DONE
  completion requests; the Hermes model engine is replaced by a no-op.
- A synthetic revoke returns HTTP 409 for stale completion, and a known
  lost lease suppresses client-side completion.
- Server kill during a lease produces an HTTP connection failure. Server
  restart preserves the not-yet-expired synthetic lease; no early redispatch.
- After synthetic clock advance and lease expiration, a second claim gets
  new identity/token. Old token or old claim is rejected with HTTP 409.
- Final restart retains DONE/CANCELLED/DONE states. Test artifacts and
  disposable Docker containers/tags are removed.
- Expected evidence: 12/12 physical checks, decision=HOLD, real tasks=0,
  provider requests=0, production deployment authorized=false.

## Important unresolved release requirements

This synthetic server runs in **the same disposable Docker container** as
the worker. It is not under independent administrator/key custody, has no
real network partition, independent clock, external witnesses, quorum,
rollback-resistant checkpoint or real concurrent worker race protection.
The fake server's journal and test token strings are not production auth.

Production Hermes broad RW SSD/knowledge ancestors remain the P0 issue
tracked in auto-assist #414. This work does not remove them or authorize
cutover. Independent signed image provenance, owner-approved end-to-end
execution, independently administered revocation/fencing and external
review remain missing. Preserve HOLD and operator approval requirements.

Next controlled validation: distinct network namespaces/hosts with a
separately controlled fake server, actual timeout/retry failure injection,
concurrent worker lease race and old-process completion denial, still with
zero access to production data, providers and admin credentials.
