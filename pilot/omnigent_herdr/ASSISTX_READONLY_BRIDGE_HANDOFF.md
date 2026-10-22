# AssistX → Herdr/Omnigent read-only model-inventory bridge

**Physical research checkpoint: 2026-10-20**
**Decision: STAGED DIAGNOSTICS / RELEASE HOLD / NO MODEL OR AGENT DISPATCH**

## What was deployed

Fleet Commander staged `assistx_readonly_bridge.py` and
`test_assistx_readonly_bridge.py` on x1-370 and Beelink alongside the
previous `assistx_operator_probe.py` in the **operator-owned** path:

```text
/home/scott/.local/share/assistx-herdr-readonly-20261020/
```

Files are 0600, staged parent directories are 0700, and each physical host
has an owner-private `latest_bridge.json`. The bridge is one-shot and
read-only. **No Compose change, production image update, service restart,
model load, application credential, token, agent, task, webhook, SSH sweep,
inference request, DNS, firewall or Tailscale configuration change is
part of this deployment.**

To recheck from an ordinary authorized host shell:

```sh
python3 -B /home/scott/.local/share/assistx-herdr-readonly-20261020/assistx_readonly_bridge.py --json
```

This is operator-only evidence, **not** a Herdr execution provider or
automatic release gate.

## Exact read-only scope

The bridge performs fixed loopback-only GETs; no arbitrary URL is accepted,
redirects are denied, proxy environment settings are ignored, and every
response body is size-bounded and parsed with duplicate-field rejection:

| Local URL | Purpose | Exported data |
| --- | --- | --- |
| `127.0.0.1:8000/openapi.json` | Confirm expected AssistX title/routes and declared auth for whoami + runtime catalog | Boolean identity check |
| `127.0.0.1:8088/openapi.json` | Confirm expected Auto-Router title/routes | Boolean identity check |
| `127.0.0.1:8000/api/v1/auth/whoami` | Verify anonymous denial | HTTP status only |
| `127.0.0.1:8000/api/v1/runtime/catalog` | Verify anonymous denial | HTTP status only |
| `127.0.0.1:8088/v1/models` | Read bounded public model advertisement | Model **count only** (not IDs) |

The bridge incorporates the preceding localhost-only operator health
observations. It does not expose model identifiers or provider URLs,
attempt to authenticate, read user prompts, send credentials, or contact a
model-generation endpoint.

**Every output includes**
`decision=HOLD`,
`production_deployment_authorized=false`,
`agent_dispatch_authorized=false`,
`model_egress_authorized=false`,
`authenticated_catalog_access_verified=false`,
`loaded_model_residency_verified=false`,
`source_owner_bound_execution_verified=false`,
`provider_requests_forwarded=0`, and
`automatic_retry_or_refund_authorized=false`.

The bridge does not create a signed independently custodied attestation.
Both OpenAPI title and unauthenticated health are observational, not a
cryptographic production build identity.

## Physical evidence

- **x1-370:** AssistX and Auto-Router health both HTTP 200. OpenAPI
  titles match `AssistX API & UI` and `auto-router`. Without
  credentials, AssistX `/api/v1/auth/whoami` and
  `/api/v1/runtime/catalog` return 401. Auto-Router `/v1/models`
  advertises **11 entries**; no model identities exported in the
  bridge record. Advertised does **not** mean resident/LOADED,
  owned, authorized, quota-qualified, or callable.
- **Beelink:** AssistX/Auto-Router/recovery processes are visible to
  host process inspection, but ports 8000 and 8088 remain unreachable
  from ordinary `scott`'s host network namespace. Bridge reports
  identities **unverified** and count **unknown**, not zero.
  Existing Tailscale Serve root routes to localhost 8081; no
  proxy change was attempted.
- **Unit tests:** 21 bridge-focused tests pass, including denied
  anonymous auth, spoofed OpenAPI title/security, malformed/oversized
  model rows, missing networking, boolean-as-status confusion,
  forbidden URL and all-green-but-never-authorized outcomes.
  The combined previous preflight + bridge suite is **35/35 passing
  independently on both x1-370 and Beelink**.
- **Full pilot suite on x1:** **727/727 tests PASS** (706 before bridge).
- **Staged bridge SHA-256 on both hosts:**
  `8ae80e13992737b6f4c70c3d6c6b01df4eb9a1d5404af1267ea7e71b79dc6dfa`.
  This matches the tested x1 source file; it is **not** an
  independent image/provenance witness.

## Existing AssistX/Hermes release blockers

Read-only `docker inspect` identified x1's running `assistx-api`
and `assistx-worker` as healthy and `assistx-hermes-adapter` as running,
but the images lack OCI source-revision labels; a mutable image tag or
local digest is not proof of independently reviewed source provenance.
The actual Hermes Compose deployment uses:

```text
/home/scott/git/auto-assist-deploy-20261016/docker-compose.yml
/home/scott/git/auto-assist-deploy-20261016/compose.override.yml
```

The Hermes container has **16 mounts, 9 reported writable**, including
broad RW aliases of `/home/scott/knowledge` and
`/media/scott/SSD_4TB`, which enclose the historical Fleet Watchdog
evidence path. This is tracked in
[auto-assist P0 #414](https://github.com/scottjoyner/auto-assist/issues/414).
No container file mutation was attempted, and writable Docker metadata
alone does not prove an actual path-write bypass.

The physical source/worker transactions in the Herdr/Omnigent pilot remain
SYNTHETIC and SAME-FLEET-ADMIN controlled: restoring an older resource
database **and** the caller's older trusted head defeats the local
anti-replay witness. Lenovo's separate 15-source SAME-HOST agent admission
is HOLD because AppArmor sandbox approval, memory headroom and real model
readiness remain unresolved. Neither the x1 nor Beelink bridge run can
promote an agent or bypass AssistX authorization.

## Next smallest safe release step

1. Independently bind x1 and Beelink's **running** AssistX/Router image
   IDs to pinned Git source/build revisions and operator-approved
   deployment/rollback instructions.
2. Remediate Hermes writable mount scope in a disposable deployment,
   preserving legitimate Hermes read and write workflows; require
   explicit operator approval before any production Compose change.
3. Prove authenticated, owner-bound AssistX AVAILABLE versus LOADED
   model catalog, per-user quota, source-owned receipts, revocation and
   external monotonic custody. No live model turns have been issued.
4. Requalify the intended sandboxed Herdr host and the actual assistant
   execution contract before connecting a runtime.

**No merge / no production deploy / no model/provider forwarding /
no live agent / no automatic retry or refund.**
