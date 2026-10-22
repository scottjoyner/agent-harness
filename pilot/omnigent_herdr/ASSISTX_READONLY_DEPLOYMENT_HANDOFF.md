# AssistX + Herdr/Omnigent: read-only fleet deployment qualification

**Fleet Commander physical checkpoint: October 20, 2026**
**Status: operational preflight STAGED; agent/model/provider release HOLD**

## Scope of this deployment

`assistx_operator_probe.py` is a **one-shot, standard-library-only
diagnostic** staged as a private user-owned tool on the physical fleet.
It does NOT deploy or restart the AssistX API, Auto-Router, Herdr or
Omnigent. It opens NO listening port, sends NO authentication token,
does NO model loading, SSH scanning, agent startup, process termination,
provider inference, shell execution or source effect, and does NOT
modify Docker, Tailscale Serve, AppArmor or production configuration.

Fixed observations are intentionally limited to:

- Presence (not identity or build attestation) of process command
  patterns for `assistx.api_router:app`,
  `auto_router.main_live:app`, and
  `assistx.recovery_island_agent_hardened`.
- HTTP **status codes only**, with no response body, for exact
  fixed local URLs: `http://127.0.0.1:8000/health`,
  `http://127.0.0.1:8088/health`, and
  `http://127.0.0.1:8081/health`.
  No CLI-supplied URL, redirects, HTTP proxy, arbitrary Tailnet
  network call or secret-bearing request is allowed.
- Existing `tailscale serve status` root forwarding port, without
  recording Tailnet hostnames or changing Serve configuration.

Outputs contain only bounded boolean/status/port evidence,
explicit blockers, `decision=HOLD`,
`production_deployment_authorized=false`,
`agent_dispatch_authorized=false`,
`provider_requests_forwarded=0`, and no execution grant.
Even all-green localhost 200 responses remain HOLD until
authenticated source authority and exact running image/commit
binding have been independently verified.

Tests exercise the all-green-but-still-HOLD outcome, unavailable
services, a 404 proxy, 401/403/500-style response statuses,
wrong process/status data types, missing evidence, attempted
false dispatch/admission and arbitrary URL rejection.

## Read-only physical discovery

**Beelink**
- Separate rootless `assistx` user processes show
  `uvicorn assistx.api_router:app --port 8000`,
  `uvicorn auto_router.main_live:app --port 8088`, and
  a separate recovery-island process.
- The ordinary `scott` host namespace could NOT connect to
  `127.0.0.1:8000/health` or
  `127.0.0.1:8088/health`. Their container/network namespace
  bindings were not independently inspected, and they must not
  be guessed from host process presence.
- Existing Tailscale Serve HTTPS is reachable, with its **root
  path proxied to `127.0.0.1:8081`**, not directly to port
  8000. That backend responds **404** for `/health` and
  **302** at `/`. The root proxy could intentionally serve
  a separate application; it was left unchanged.
- The `scott` account cannot inspect the separate rootless
  Docker socket, so this run did NOT prove which AssistX
  image, source commit or host mounts were active.

**x1-370**
- `http://127.0.0.1:8000/health` returned **200**;
  `http://127.0.0.1:8088/health` returned **200**.
  Read-only `/openapi.json` checks identified the API titles
  as **`AssistX API & UI`** and **`auto-router`**
  respectively, without logging the full schema.
- `GET /api/v1/auth/whoami` with NO credentials on local
  AssistX returned **401**. No authenticated catalog,
  available/loaded model proof or owner-specific task execution
  was attempted.
- Existing x1 Tailnet Serve has a root proxy to 8081, BUT
  distinct explicit `/health`, identity and runtime/catalog
  routes are forwarded to x1's 8000 AssistX API.
  **Do not confuse the default Serve root with the explicit
  AssistX health route** or change the root proxy as a
  speculative fix. Those path-specific routes and auth still
  require physical trusted access checks before an agent uses
  them.

These observations do not establish that the two hosts run the
same AssistX revision or can authorize distributed agent effects.
Running `python3 .../assistx_operator_probe.py --json` on a
host produces local evidence only, and every execution is
**explicitly HOLD**, regardless of healthy probe results.

## Deployment location and evidence

Use the exact user-scoped path
`$HOME/.local/share/assistx-herdr-readonly-20261020/` on the
respective node. Files are 0600 in a 0700 directory. Only the
probe, tests and bounded JSON snapshot are staged; no service
units or host ports are created. The script runs as ordinary
operator user and needs only Python 3.

- Baseline pilot suite before this slice: **692 tests passing**.
- The added AssistX probe's **14 focused unit tests pass**,
  and the combined suite now has **706/706 passing**.
- The same focused tests passed on actual Beelink.
- A physical Beelink probe returned process presence for all
  three components, ports 8000/8088 unreachable from the
  scott host namespace, Serve backend 8081 returning 404
  for `/health`, and **HOLD**.
- On x1, API/router localhost returned 200, but the diagnostic
  retained HOLD. The x1 OpenAPI and unauthenticated-401 checks
  were run separately for service identity.
- The **actual operator account** x1 location is
  `/home/scott/.local/share/assistx-herdr-readonly-20261020/`.
  This account's `.local` path resolves to an existing SSD
  home-directory symlink, not to Fleet Commander's isolated
  `$HOME`; the isolated initial copy is not counted as the
  operational staging target.
- The Beelink location is
  `/home/scott/.local/share/assistx-herdr-readonly-20261020/`.
  Both physical node copies ran 14/14 focused tests, have
  0700 parent folders and 0600 tool/test/report files, and
  retain a sanitized `latest_probe.json` snapshot.
- Operator tool SHA-256 at staging:
  `ba7f3652314a3cc73b674d49d2222cc5c29029f6ee53332a4326981754ceb06f`.
  The focused-test script SHA-256 is
  `f6f46789c2c95eea4122ea038fa5f563d278fe28e7c094ce381dbedbb16cdb2a`.
  These SHAs are file-integrity cross-checks ONLY, not
  independent deploy attestations.

**After copy, always check the staged file SHA and re-run the
focused tests. Do not automatically launch or enroll any agent.**

## Safety and dependencies before AssistX can admit Herdr/Omnigent

1. Confirm *exact running* AssistX/Auto-Router container image
   digest, source SHA, binding and reliable authenticated health
   **from the same network namespace where the service executes**.
   Local process presence, 200 health and a Tailnet route do
   not prove that binding.
2. Resolve the relevant AssistX security/release issues,
   including supervisor admission and cross-repository effect
   authority. No draft research PR is deployment authorization.
3. Verify actual authenticated model catalog and differentiate
   **available** from **physically loaded** models, with fresh
   per-user authorization and source-owned transaction receipts.
   Do not load models or provide tokens via this diagnostic.
4. Provide independently controlled, rollback-resistant
   resource revocation/checkpoint and safe retry/fencing proofs.
   The source-local synthetic SQLite result from Herdr's
   guarded pilot cannot be promoted to that role.
5. Qualify target agent host. Lenovo's 15-source SAME-HOST
   admission remains **HOLD** due to <8 GiB available memory,
   unapproved narrow AppArmor bwrap profile, unverified
   physical sandbox and owner/model/provider readiness.
   Destroyer lacks suitable RAM/swap for multi-agent inference.

**DO NOT route the read-only AssistX operator probe into
an authenticated chat or production execution endpoint. It is
a staging diagnostic, not a production connector.**

**PR DRAFT / NO MERGE / NO PRODUCTION SERVICE RESTART /
NO MODEL OR AGENT DISPATCH / NO AUTOMATIC RETRY OR REFUND.**
