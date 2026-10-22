# V2 signed synthetic source-denial fence and concurrent-worker tests

**Fleet Commander research checkpoint: October 20, 2026**
**Release decision: PR #18 DRAFT / HOLD / no merge, agent dispatch or deployment**

## Problem and the previously missing failure mode

The V2 disposable synthetic source on Destroyer uses a private two-unit
SQLite balance and one Ed25519 signing key, separate from x1-370 worker
lease/outbox databases and worker-signed requests. It verifies a caller's
exact current source head within `BEGIN IMMEDIATE` and commits the
balance reduction and source-signed receipt in one LOCAL transaction.

This gate correctly prevents two requests **both using the same old
source head** from committing concurrently. However, a previously
rejected, distinct losing operation could be resubmitted afterward
with the **new winning head** and treated as a new debit. Before
this slice, duplicate *committed* operation IDs were denied, but
previously *declined* stale operation IDs were not remembered.

This creates a resource-local *stale attempt rebase* risk. A fresh
head obtained from the source after a race must not, by itself,
license a retry/refund, source re-debit, model request or agent.

## Source-local denial fence

`v2_source_transaction_fixture.py` now has a dedicated synthetic
SQLite schema version 2 with a bounded, independently checked
`denied_intents` table. For a request with an old head that is a
**verified exact prefix** of the current source history, and whose
operation ID has not already committed or been denied, the source:

1. Opens its own SQLite `BEGIN IMMEDIATE` writer transaction,
   verifies its full existing source receipt chain, balance and
   previously signed denial fences.
2. Creates an Ed25519-signed, domain-separated
   `synthetic_source_stale_attempt_denied` fence binding source
   signer pin, operation ID, request-intent SHA-256, *requested
   old head*, *observed winning head*, and mandatory
   `retry_authorized=false`,
   `agent_dispatch_authorized=false`,
   `provider_requests_forwarded=0`.
3. Persists that signed fence to its source-owned, private SQLite
   file and returns **only a generic denial**, never a grant.
4. Refuses any future `commit` using that operation ID,
   **even with a newly obtained valid source head**. Existing
   committed operation IDs remain rejected by the separate
   committed-record uniqueness control.

No signed denial is created from an unknown, forged or future
source head. At most **four** denied operation IDs are stored
per disposable two-unit resource, keeping the experiment bounded.
A full denial table makes further stale attempts fail closed.

Every source read/open operation validates the complete signed
denial chain, the exact prefix links to the original source
commit history, the key/pin, canonical row serialization,
signature integrity, and non-grant flags. The denial persists
even if an unrelated, separately authorized second synthetic
source debit later advances the source to another head.

The new `fence` read-only CLI and `inspect_stale_denial`
require the exact signed-source operation ID/intent, claimed
old source head, and independently retained *current source
head*. They return the source-signed stale-attempt fence,
which an off-source verifier can check with
`verify_stale_source_fence`. They do not write a record,
consult a provider, issue a new debit, or change ownership.

This is **local persistent operation-ID fencing**. It is **not**
proof of independent custodian authority, identity-level or
session-level owner binding, immutable external high-water,
distributed exactly-once execution, or protection against
a malicious worker issuing a **different operation ID**.

## Three real concurrent SSH source races

`v2_source_fork_race_probe.py` prepares synthetic worker-signed
lease/outbox intents under separate private x1 scratch roots,
with separate ephemeral Ed25519 worker/observer keys. It launches
two one-shot SSH requests concurrently to **the same** new
Destroyer source signer/SQLite file, with both presenting the
genesis `(0, ZERO)` source head. A Python two-thread barrier
aligns the clients; Destroyer's two separate Python processes
compete for the same SQLite `BEGIN IMMEDIATE` writer lock.

| Scenario | Physically verified outcome |
| --- | --- |
| Two distinct signed operations share the same genesis head | Exactly one debit commits. Other operation receives denial; source signs/records permanent local stale-attempt fence. Rebase attempt with winner's current head denied. |
| Two distinct signed workers use the same operation ID but different signed intent digests | Exactly one debit commits. A competing request with already committed operation ID is denied; no fabricated separate stale fence. |
| Exact same signed worker publication is delivered twice concurrently | Exactly one debit commits. Both parties may verify the one original receipt *read-only* as evidence of that same operation, but cannot re-debit. |

For the distinct loser, an attempt to "borrow" the winner's
signed receipt fails worker/source operation/intent matching
and is classified `QUARANTINED_HOLD`. For the **exact duplicate**
delivery, the legitimately identical original request is allowed
to verify the single original source receipt as
`SOURCE_LOCAL_COMMITTED_HOLD`, but all execution and retries
remain DENIED.

In every physical scenario, the source shows **one** signed
receipt, the fake balance is 1 of 2 units, source local debit
cannot be repeated, provider requests remain zero and all
agent/model/egress flags remain false.

## Regression evidence

- **692/692** full pilot Python tests PASS on x1-370 (677
  before this slice, 15 new tests).
- **108/108** focused source, race, journal and crash-window
  tests PASS on the actual Destroyer physical host.
- All **three** new concurrent physical x1↔Destroyer
  source-race scenarios PASS with separate source scratch
  roots and disposal of source keys and DBs between runs.
- Existing full x1↔Destroyer source debit + worker journal
  crash/acknowledgment canary PASS with source fencing added.
  Its intentionally stale operation now has a unique ID
  and a signed denial; the subsequent separate **valid**
  operation uses a different ID and still succeeds.
- Additional unit tests verify signed fence freshness/pinning,
  tampering, unknown source heads, no false denial on an
  already committed operation, fence retention after another
  legitimate synthetic debit, persistence/reopen, and
  actual source SQLite rollback negative controls.

Source `APP_VER` increased to **2** in this disposable
research fixture to fail closed on older or mismatched schemas.
No production schema or migration has been attempted.

## Mandatory NO-GO / remaining authority gaps

- The source signing key and source SQLite state are on
  Destroyer under the **same fleet administrator** as x1,
  Lenovo and the worker's claimed high-water. Restoring an
  older valid source SQLite image **and its corresponding
  old externally expected head** undoes the local denial.
  This exact rollback loophole remains covered by a
  unit-test counterexample.
- The denial is by **operation ID**, not a separately
  verified production user, request ownership, lease
  identity or source-authoritative transaction ID. A
  compromised caller could try a newly minted operation
  ID. No signed attempt authorization from a genuinely
  independent resource owner is established.
- `BEGIN IMMEDIATE` serializes this single SQLite source
  but does not solve multi-source, cross-node split-brain
  or network-partition fencing. No true power-loss,
  clock rollback, host reboot, hardware tampering or
  independent-operator test occurred.
- The worker lease/outbox and source balance/denial are
  still distinct local SQLite transactions, not a single
  distributed atomic, exactly-once resource effect.
- The synthetic source is an artificial two-unit counter,
  **not** an actual provider, agent, billing, quota or
  production database.
- Lenovo's separate **15-source SAME-HOST admission receipt
  remains HOLD**: admin-approved narrow AppArmor profile
  not loaded, available memory below 8 GiB floor, exact
  model/provider quotas, identity and execution receipts,
  network egress and physical device acceptance unproven.
  Destroyer has little available memory/swap for hosting
  production agents.

**Smallest safe next milestone:** out-of-fleet independently
administered resource signer and protected external monotonic
checkpoint/revocation roots, signed owner-bound source attempt
identifiers, then authentic crash/partition/split-brain
validation. No merge, service rollout, agent model invocation,
source production mutation, AppArmor weakening, autoload,
retry, refund or unfence is authorized.

**DRAFT / HOLD / NO MERGE / NO DEPLOY / NO LIVE AGENT DISPATCH.**
