# Hermes rootless candidate: isolated localhost HTTP lifecycle

Date: 2026-10-21. Scope: agent-harness PR #18, x1-370.
Research-only; no production Docker Compose, credentials or services changed.

## Reproduction

Dry run (always HOLD; no Docker):

~~~sh
python3 pilot/omnigent_herdr/assistx_hermes_fake_http_canary.py --json
~~~

Explicit opt-in only on x1-370:

~~~sh
python3 pilot/omnigent_herdr/assistx_hermes_fake_http_canary.py --physical-disposable-http-restart --json
python3 -m unittest discover -s pilot/omnigent_herdr -p 'test_*.py'
~~~

## Verified physical controls

- Pinned running Hermes image:
  sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307.
- Two identical offline overlay builds:
  sha256:9d5c1894398c0d62943f6aa5eb2dddc3d5b6d808138b2b02bbd84fdb66c0aa23.
- Normal rootless entrypoint executes actual image-baked strict executor main.
- Docker network=none; localhost HTTP only. The fake HTTP server is a
  thread inside the disposable container, not a separate service.
- Nonroot, read-only rootfs and synthetic knowledge, writable synthetic
  workspace, dropped Linux caps, no production mounts.
- Real HTTP poll, claim, task token, heartbeat, context, memory, completion.
- Second synthetic claim revoked: HTTP heartbeat returns 409; client blocks
  completion on known lease loss; stale forced HTTP completion returns 409.
- Docker start of the stopped disposable container runs a fresh Python process;
  synthetic completion journal persists in scratch, and poll has zero tasks.
- All disposable Docker tags and container removed. Production Hermes stayed
  running and unchanged. Zero provider requests and zero real task execution.

## Remaining gates — HOLD

1. Production Hermes still has broad RW SSD/knowledge mounts (AssistX #414).
2. No independently signed source/image provenance or owner-authorized task.
3. No live multi-agent execution, actual Hermes binary, tool calls or provider.
4. In-process localhost fake server does not prove remote API fault tolerance,
   clock independence, adversarial lease races or independent revocation.
5. The synthetic journal is not authoritative production fencing/custody.

Next: separately isolated fake API process, bounded crash/restart and
lease-fencing failure injections. Do not deploy or modify production Compose
without independent review, provenance and approved rollback plan.
