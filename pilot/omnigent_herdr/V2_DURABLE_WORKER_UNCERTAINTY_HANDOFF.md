# V2 durable worker-side uncertainty journal and source readback

**Fleet Commander checkpoint: October 20, 2026**
**PR #18: draft / no merge / no production deployment / agent dispatch HOLD**

## Why this exists

Previously the worker on x1-370 could classify a lost response from
the disposable Destroyer synthetic resource as `UNCERTAIN_HOLD`,
`QUARANTINED_HOLD`, or `SOURCE_LOCAL_COMMITTED_HOLD`. The
classifier correctly prohibited retry/refund/unfencing/model
execution, but the classification was **not itself persisted**.
Restarted callers needed to retain the worker's signed intent and
source-public-key trust arguments without treating mutable cached
status as admission authority.

This slice adds `v2_uncertainty_journal.py`, an opt-in,
**research-only worker-side SQLite HOLD journal**. It is separate
from x1's original atomic worker lease/outbox and Destroyer's
synthetic source DB. The journal cannot call a model, retry a
resource action or change either of those databases.

## Journal contents and invariants

- One private `$HOME/.cache/og-v2-uncertain-XXXXXX/` scratch
  directory (0600 regular SQLite file, 0700 parent, same UID, no
  symlink/hardlink), with bounded application-ID/version schema,
  `BEGIN IMMEDIATE` writes and `synchronous=FULL`.
  Research temporary names allow the alphanumeric/underscore
  candidate pattern used by Python's `tempfile` helper.
- Genesis captures the exact **original worker Ed25519-signed
  publication**, worker/observer identity, worker/outbox head,
  worker before/after state hashes, source public key pin, and
  the explicitly supplied previously trusted source record head.
  Genesis verifies the signed worker publication before writing.
- Every new observation writes the **complete returned source
  response** (or explicitly no response) plus the result of
  reclassifying that source evidence against the immutable
  genesis. Stored observations are hash-linked and sequential.
- Every reopen **re-verifies the signed worker publication**,
  every stored classification, valid cryptographic source receipts,
  canonical JSON, predecessor hash, schema and public pin
  continuity, instead of trusting the saved textual status.
- `UNCERTAIN_HOLD` can accumulate bounded no-evidence reports,
  then progress to a genuinely verified
  `SOURCE_LOCAL_COMMITTED_HOLD` or terminal
  `QUARANTINED_HOLD`. Quarantined evidence may not be
  promoted by a later replay. A verified source-local commit
  cannot be downgraded to no effect or replaced by a
  conflicting source receipt.
- Mutating `observe` requires the **exact current caller-held
  journal head**. Read-only `inspect` requires an exact trusted
  context digest and current journal head. A one-shot `inspect`
  CLI allows a **fresh Python process** to reopen and validate
  the signed evidence; it never dispatches or mutates the DB.
- **Every returned record is HOLD**, even when
  `source_local_commit_verified=true`:
  `agent_dispatch_authorized=false`,
  `model_egress_authorized=false`,
  `automatic_retry_authorized=false`,
  `automatic_refund_authorized=false`,
  `lease_unfence_authorized=false`,
  `worker_source_distributed_atomicity_verified=false`, and
  `provider_requests_forwarded=0`.

### What it is not

A hash-linked **local** SQLite journal is NOT an immutable,
independently witnessed, externally monotonic trust log. A shared
fleet administrator can restore an old valid SQLite database
and its caller-held expected head together. The tests
deliberately reproduce that weakness. There is no independent
authority provisioning, trusted clock, protected hardware
counter, durable off-fleet high-water, signer separation by
administrator or authenticated production provider.

The worker lease debit + outbox, the source balance debit +
receipt, the separate observer/Lenovo custody steps and this
new journal are all **different local transactions**. No
distributed exactly-once effect is established. After any
missing response, the only permitted action remains bounded
read-only evidence recovery, never automatic resubmission.

## Tested behavior

Dedicated `test_v2_uncertainty_journal.py` tests include:

- Genesis signature and source pin scope verification;
  persisted uncertain → verified source-local HOLD transition.
- A signed source receipt is rechecked on every reopen;
  forged receipts, wrong signer, changed operation/head,
  corrupt observation/verdict/worker-signature rows fail closed.
- Incorrect context SHA or prior head, stale sequence, unsafe
  paths, symlink/hardlink/permissive files, deleted SQLite
  state and duplicate bootstrap are denied.
- Five simultaneous writers referencing the same prior
  head yield one committed observation and four denials.
- Terminal quarantine, no downgrade from confirmed
  source-local commit, no refund or retry after success.
- **Actual older journal DB image restoration** is denied
  against a retained newer head, yet **old DB + old head**
  still passes (explicit unresolved rollback counterexample).

**Fleet Commander physical proof (x1-370 → Destroyer):**

1. x1 commits one synthetic worker outbox lease intent,
   signs it, and creates a separate private uncertainty
   journal. Destroyer owns a distinct disposable source
   signer and private fake two-unit SQLite resource.
2. A synthetic source process is deliberately exited
   before local commit. Source readback lacks an effect,
   so the worker journal records `UNCERTAIN_HOLD`.
   Missing source evidence does NOT authorize retry.
3. A separate synthetic source process is exited after
   its **source-local SQLite commit**, but before
   returning its response. x1 again records missing
   evidence as `UNCERTAIN_HOLD`.
4. A fresh SSH/Python process performs read-only source
   recovery. Its source-signed receipt matches x1's
   independently signed worker intent and caller-held
   prior source head. The x1 journal commits
   `SOURCE_LOCAL_COMMITTED_HOLD` with source evidence,
   without repeating any debit.
5. A fresh **x1 Python process** independently reopens
   that private journal and verifies the current
   journal head/status. A stale journal head is denied.
6. Two consecutive full physical runs with **separate**
   disposable remote source directories and signing
   keys passed. The source correctly refused an
   attempted second bootstrap in an already used
   remote root; the repeat harness was corrected
   to create fresh roots, not relax the source's
   exclusive-creation protection.

**Evidence:** Full pilot suite **660/660 passing** after
this final fixture update (636 prior + 24 new focused
tests); 74 source/recovery/journal focused tests passed
on Destroyer before the last two unit additions, and
two full physical canaries passed on x1/Destroyer.
No global AppArmor changes, no production DB writes,
no provider calls, no agent starts, no new services
and no unrelated process terminations occurred.

## Remaining release gates

- **Independent administrative custody and rollback-resistant
  external high-water** are absent. The source,
  worker, observer, witness and mock authority signatures
  remain accessible under the same fleet operator.
- Production **resource-owned signed transaction IDs**,
  out-of-band revocation and external monotonic observer
  checkpoint remain unverified.
- Only bounded research **process** crash injection,
  not physical power loss or genuine network isolation,
  was tested. Crash consistency and revocation/fencing
  across real partitions remain unverified.
- The separate Lenovo **15-source SAME-HOST admission
  receipt remains HOLD**: approved AppArmor sandbox
  profile absent, <=8 GiB available memory gate,
  model catalog/quotas, owner-bound real execution
  receipts and restricted egress not qualified.
  Destroyer lacks sufficient free swap/RAM to host
  multiple live agent workloads.
- PR #18 remains **DRAFT, unmerged**, and the code
  must not be wired to an active model router,
  shell executor, provider client or deployment.

**NO DEPLOY / NO MERGE / NO LIVE AGENT / NO MODEL
FORWARDING / NO AUTO RETRY OR REFUND.**


## Follow-on: lost acknowledgment from the worker journal itself

`v2_uncertainty_journal.py` now accepts a scoped, **read-only**
`reconcile` when an `observe` operation could have committed
but its ACK was lost. The caller must supply the original source
response, exact previous journal head and immutable signed-worker
genesis context hash. Only the immediately following stored
observation may be recovered, after complete signature/hash-chain
revalidation. A missing row or mismatch is unresolved HOLD, not
permission to retry or refund.

Both before-commit and after-commit **disposable process exits**
were injected in Fleet Commander physical x1→Destroyer research.
No source debit or worker lease was reissued. Same-admin journal
rollback and distributed transaction atomicity remain unproven.
See [V2_LOST_WORKER_JOURNAL_ACK_HANDOFF.md](V2_LOST_WORKER_JOURNAL_ACK_HANDOFF.md).
