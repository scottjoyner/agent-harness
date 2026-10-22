# V2 synthetic source-side committed receipt — physical Fleet Commander handoff

**October 20, 2026 — draft PR / HOLD / no merge, deployment or agent dispatch.**

## Why this is a different milestone

Previously a V2 worker created one local HMAC-authenticated
`AtomicSyntheticLeaseOutbox` entry and signed an Ed25519
publication. Although its debit/outbox intent was atomically
committed **inside the worker's local SQLite database**, the
worker controlled both the state and publication key. The observer
could prove the worker's signature but not that a separately held
resource database had recorded an effect.

This slice creates a **distinct, disposable research resource**
on Destroyer. Destroyer owns its own 2-unit synthetic balance,
SQLite database, and ephemeral Ed25519 signing key. The worker
on x1 sends a **synthetic intent** with an operation identifier
and request digest; Destroyer never accepts a pre-signed source
receipt or a caller-supplied post-transaction balance.

It is a deliberately limited proof about a **fake resource**, not
a real model, API call, broker admission, production account,
storage side effect, independent custodian, or distributed atomic
transaction.

## Implemented controls

`v2_source_transaction_fixture.py` provides three one-shot commands:
`bootstrap`, `commit`, and `inspect`. The sole allowed destination
is a newly created owner-private
`$HOME/.cache/og-v2-source-XXXXXX/` directory:

- **Bootstrap** creates an exclusive 0600 Ed25519 private key and
  a separate 0600 SQLite DB (dedicated application ID/version,
  `journal_mode=DELETE`, `synchronous=FULL`, owner-only root).
  It starts with an explicit two-unit synthetic balance.
- **Commit** requires the exact prior caller-held source head,
  a 32-hex operation identifier, and 64-hex worker-intent SHA-256.
  With `BEGIN IMMEDIATE`, it checks the complete existing
  receipt/debit history, refuses duplicate operations and stale
  heads, decrements **one synthetic unit**, generates a
  domain-separated source-signed receipt, inserts that receipt
  in `debits`, updates `resource`, revalidates the complete
  SQLite state, then commits. On an injected SQL failure after
  receipt insertion but before balance update, **both changes
  are rolled back**. Only two source operations are permitted.
- **Inspect** requires the exact source record number and receipt
  SHA-256; a fresh Python/SSH process reopens the actual database,
  validates schema, private file permissions, link count,
  signing-key continuity, all Ed25519 receipt signatures,
  record order and hash linkage, balances/count and current
  persisted head. It returns no dispatch grant.
- **Source signature verification** on x1 pins the source public key,
  operation ID, request digest, source record head, and synthetic
  before/after digest.
- **Cross-boundary verification** separately verifies the
  worker's signed V2 publication **and** Destroyer's signed
  local source receipt. The two claims must contain the
  identical operation ID and request SHA-256. A modified
  signature, substituted key or mismatched scope is denied.

This does not couple the x1 worker-local lease debit and Destroyer
resource-local debit into one transaction. Two local SQLite commits
can diverge during partitions or crashes. A signed source receipt
is evidence of a **synthetic source-local transaction**, provided
the source root has not been tampered with; it is not a guarantee
that any production resource or provider action happened.

The source key exists only in Destroyer's disposable scratch root
for the research test. The worker and test harness can read the
source **public** key over existing strict-known-host SSH. The
source key is NOT independently custodied: the same fleet operator
has SSH access to Destroyer and can replace its database and
test key. No resident network listener or service is created.

## Actual physical evidence

`v2_source_transaction_probe.py` used Fleet Commander on x1-370
to create a synthetic V2 worker debit/outbox intent. It sent
that intent over already pinned known-host SSH to new one-shot
Python processes on Destroyer, where the separate local
resource SQLite database and signing key existed.

| Check | Observed outcome |
| --- | --- |
| New Destroyer key and private source database | PASS |
| First 2→1 synthetic balance decrement and Ed25519 receipt saved in same local SQLite transaction | PASS |
| x1 verified source Ed25519 signature with pinned fixture key | PASS |
| Worker signed intent matched source operation ID and request SHA-256 | PASS |
| **Separate SSH/Python process** reopened Destroyer DB and verified signed receipt/balance | PASS |
| Duplicate source operation ID or stale prior head | DENIED |
| Forged worker publication, forged source receipt, wrong source key | DENIED |
| Second synthetic decrement 1→0, then third attempted debit | Second PASS / third DENIED |
| **Actually restored old valid Destroyer SQLite image**, checked with newer head | DENIED |
| Same restored old image **with the old expected head** | ACCEPTED — required rollback counterexample |
| Restored newest test image; checked newest signed head | PASS |
| SQLite trigger abort between receipt insertion and balance update | BOTH WRITES ROLLED BACK |
| Signing-key substitution, missing/corrupt records, unsafe file modes/links | DENIED in focused unit tests |
| Live agent dispatch, model/provider calls, real repo/storage effects | NONE |

The **source fixture's focused tests passed on Destroyer**, as did
the physical x1↔Destroyer test. The complete pilot Python suite on
x1 passed. Both the local x1 scratch and Destroyer source directory
and temporary source key are removed by the experiment cleanup.

The physical test reopens a process but **does not reboot a host**.
The physical run does not attest original worker resource effects
from an independent administrator, and it does not establish
exactly-once effects across hosts.

## Admission and security limitations

**NO-GO remains mandatory** for live Herdr/Omnigent agents:

1. The resource is synthetic (a local 2-unit counter), **not a
   production broker or model API**. The source host is shared
   administratively with x1 and Lenovo.
2. The source private key and SQLite record are not independently
   protected. Restoring an old source database **and the old
   claimed expected head** succeeds. If the same admin controls
   both source and head, rollback remains possible.
3. Matching signed worker intent/source receipt does NOT make
   separate worker and source transactions distributed-atomic,
   and does not permit automatic retry, refund or effect replay.
4. The existing 15-source Lenovo SAME-HOST agent admission
   receipt remains HOLD; the new cross-host source trial is
   **not** added as green production qualification evidence.
   Lenovo's vendor AppArmor profile/sandbox, >=8 GiB available
   RAM, exact model/provider quotas and owner-bound receipts
   remain unresolved. Destroyer is memory/swap-ineligible
   for agent workloads.
5. No hardware counter, independent operator, protected outside
   witness root, durable independently administered revocation,
   real source transaction signing authority, external clock,
   real provider access, or production deployment was established.

## Smallest next safe step

The NEXT credible authorization evidence is a **resource-owned
production transaction ID** signed by an authorized source
process with keys outside the worker/operator's administrative
control, along with independent monotonic high-water,
trusted key/revocation custody and crash/partition testing.
Only then consider mapping verified source receipts to a real
provider/router admission protocol, and keep user/admin
approval for any host sandbox policy change.

**Research source checks are not execution grants. DRAFT / HOLD /
NO-MERGE / NO DEPLOY / NO MODEL FORWARDING / NO AGENT DISPATCH.**


## Follow-on: lost-response and cross-host crash-window handling

A bounded read-only source `recover` API and
`v2_cross_host_uncertainty.py` now require the original
worker-signed intent and caller-held prior source head before
accepting a locally committed synthetic source receipt.
No or malformed evidence is never a license to retry/refund
the worker or unfence the operation. Physical Fleet Commander
tests simulated a source-process exit both before commit and
after local commit but before returning its reply; a new
SSH/Python source process recovered the latter exact receipt
without debiting again. **Worker/source distributed atomicity
and independent source custody remain unproved.**
See [V2_SOURCE_CRASH_PARTITION_RECOVERY_HANDOFF.md](V2_SOURCE_CRASH_PARTITION_RECOVERY_HANDOFF.md).


## Follow-on: signed source-local stale-operation fencing

A new v2 source schema retains SOURCE-SIGNED denial records when
a distinct losing operation presents a **verified stale prefix**
of the source head. This prevents rebasing that same operation
onto the winner's newer head and receiving an extra local debit.
Three physical concurrent two-worker SSH scenarios passed on
Destroyer. The source validates denial signatures/history on
reopen and offers a read-only signed `fence` inspection.
The scope is operation-ID local only, not a production owner
identity grant or globally monotonic independent authority.
See [V2_SOURCE_FORK_FENCE_HANDOFF.md](V2_SOURCE_FORK_FENCE_HANDOFF.md).
