# Free-first model capability shadow — research-only contract

**Status:** Draft research implementation, **no provider dispatch authority**.

The proof-of-concept under `realtask/free_first_shadow/` transforms recorded
synthetic model calls into a deny-only capability projection and five proposed
agent-role specializations. It **does not call a model**, read credentials,
change production routing, probe a provider, create a lease, consume quota,
restart services, deploy, merge, or purchase credits.

## October 22, 2026 research checkpoint

Fleet Commander controlled a bounded campaign on x1-370, with copies of its
research receipts on Joyner's Samsung 9100 PRO Gen5 SSD. The study did **not**
prove general-purpose model accuracy or account-level free entitlements.

- Product boundary: OpenRouter, Kilo anonymous, Cohere Direct are separate
  products; OpenCode Console and Zen are also separate products.
- OpenRouter's free research sweep stopped at **HTTP 429**, with no retries.
- Kilo returned four successful text-only synthetic JSON outputs out of
  five public model checks; one `404` remains inconclusive.
- Cohere Direct North Mini Code passed a synthetic correction and, after
  failure feedback, generated a pure interval merge function. The revised
  code was validated on 12/12 model-generated cases and 20/20
  independently constructed held-out cases.
- Nemotron Ultra produced 12 syntactically valid, reference-checked cases
  (11 distinct), and correctly rejected double-counting a synthetic
  50-request shared upstream quota.
- Poolside Laguna correctly rejected a synthetic automatic paid fallback.
- Some code/test-generation attempts returned no visible output or timed
  out. They remain **unqualified** for those roles.

All inference inputs in this study were synthetic. Cohere did not provide a
monetary cost field, and served-model identity was absent in its direct API
response. That is a **distinct attribution gap**, not evidence of a bad answer.

## Offline replay and planning interface

Assuming an externally supplied research receipt directory with supported
synthetic output files and a previously created capability projection:

```bash
python3 -m unittest -v realtask.free_first_shadow.test_shadow

python3 -m realtask.free_first_shadow.capability_evidence \
  --root /path/to/research-receipts \
  --out /path/to/new-shadow-projection.json

python3 -m realtask.free_first_shadow.role_plan \
  --projection /path/to/new-shadow-projection.json \
  --out /path/to/new-shadow-role-plan.json
```

These entrypoints are **offline only**. They fail closed on malformed request
hashes, missing output digest evidence, contradictory model identities and
missing exact role evidence. The role planner intentionally carries no runtime
network clients or API credentials.

The model names embedded in the October 22 role recipe are **historical
candidates**, not current prices, free entitlement, or a live provider
catalog. A candidate may be removed or materially changed before any later
evaluation.

## Authority boundaries

Even fully passing synthetic tests yield:

- `dispatch_allowed=false`
- `fleet_dispatch_enabled=false`
- `production_admitted=false`
- `account_lease_verified=false`
- `billing_fallback_guard_verified=false`

A future separately reviewed integration must independently prove exact
current model/free-price and account entitlement, authoritative shared upstream
quota lease and revocation fencing, no paid fallback or auto-recharge, privacy
and retention eligibility, actual served-model identity where possible,
positive free request headroom, and runtime resource isolation.

No output from this package is accepted as the AssistX/Auto-Router authoritative
provider table. It must **not** be wired to model dispatch, deployment, model
loading, or billing merely because its research tests pass.

## Scope and repeatability limits

The October 22 reproduction references synthetic artifacts held outside this
repository; raw user/account traces, model weight files, API keys, and billing
records are deliberately **not committed**. Standard-library unit tests use
only synthetic records and run without network access or a fleet node.

An internal API receipt hash proves only local content consistency. It is not a
cryptographic signer, external auditor, anti-rollback witness, or guarantee of
provider billing. Missing/unknown data always remains UNKNOWN rather than
silently becoming a PASS.
