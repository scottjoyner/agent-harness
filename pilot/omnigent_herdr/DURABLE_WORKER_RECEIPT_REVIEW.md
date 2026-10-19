# Durable synthetic worker receipts and the two-database crash gap

**Research checkpoint — October 19, 2026. Production dispatch: HOLD.**

The prior two-node Herdr/Omnigent fixture could prove that a disposable
Lenovo worker *signed* a commit or observer ACK-delivery statement.
Those worker signatures were transient and did not survive a restart.
This slice adds a second, worker-side SQLite journal that preserves
those signed statements, without connecting to production services.

## Implementation

`durable_worker_receipts.py` implements
`SyntheticWorkerReceiptJournal`. It persists a sequence of exactly
two signed record kinds from `worker_acceptance_receipt.py`:

- `synthetic_worker_commit`: binds ownerless fixture worker and observer
  roles, operation ID, request and observer-preparation SHA-256, previous
  and new synthetic lease DB state, observer generation, sequence,
  receipt number, and deny-only agent/provider flags.
- `synthetic_worker_delivery`: contains the same exact commit claims,
  plus the hashes of the observer's signed ACK and signed checkpoint.

A private, separate SQLite file has application/schema identity, one
initial state digest and pinned worker/observer public-key fingerprints.
The library requires the caller to supply a valid Ed25519 signer
separately to append. **No private Ed25519 key is stored in SQLite.**
The file is created exclusively with owner-only permissions under
an owner-only directory; reopening a missing, corrupt, symlinked,
hardlinked or wrong-key journal fails rather than silently resetting it.

Every cooperative read validates all signed records, canonical JSON,
worker/observer role separation, sequential record numbers, previous
record hashes, exact commit→delivery matching, and chained worker
state/generation transitions. A new synthetic commit is refused until
the previous commit has a matching delivery record. An attempted
duplicate, stale head or competing writer is rejected.

**Every inspect or append requires a caller-supplied exact
`(record_count, signed_journal_head_sha256)` anchor.** Returning the new
head after a write is not proof that an independent party received
it. If a commit reached the journal and the response was lost, an old
head must not be used to retry or rewrite it. Reconciliation is
operator-gated; there is no automatic recovery or refund.

The library's inspection output always says `decision=HOLD`,
`agent_dispatch_authorized=false`,
`model_egress_authorized=false`,
`provider_requests_forwarded=0`.

### Explicitly unresolved: resource authority and atomicity

A worker can generate a signed story about its own SQLite state.
Checking its signature against a public key advertised by that same
worker **does not establish independent custody or source authority**.
The journal verifies that a signed delivery record matches the signed
commit record; it does **not** independently authenticate the actual
observer ACK bytes or prove their durable publication. That requires
a separately pinned observer key and independently attested
source-side transaction identifiers.

The synthetic lease database and worker receipt journal are **two
separate SQLite databases**. Their writes are NOT crash-atomic.
Specifically:

1. The lease DB can commit a debit.
2. The process may die before writing the worker-signed commit receipt.
3. The journal still shows the earlier state; reading the lease DB
   against that expected state detects the disagreement, but does not
   prove which receipt/retry action is safe.
4. The system must fence further operations pending independently
   reviewed, source-authoritative reconciliation. **Do not silently
   retry a debit, forge a replacement receipt, or refund a quota.**

The disposable physical probe includes a *synthetic operator-provided*
signed-commit continuation after exhibiting the gap. This is a test
of journal mechanics, **not** approval for an actual recovery workflow.

A second negative result also remains: an attacker who restores a
valid older receipt DB **and the old trusted anchor** can replay a
previously valid journal state. The prototype is not tamper-proof,
does not have an independent high-water publisher, cannot prove
power-loss durability of hardware, and does not survive a compromised
same-UID or privileged file custodian.

## Tests and physical Fleet Commander evidence

`test_durable_worker_receipts.py` exercises private storage, signed
commit/delivery structure, cooperative restart, duplicate operations,
stale or missing anchors, wrong worker signer and distinct observer
role, malformed and missing records, delete/mutate attacks, and
eight-way concurrent same-head update (only one succeeds).

`durable_worker_receipts_probe.py` uses a **fresh, disposable**
Ed25519 worker signer, separate observer signer, HMAC fixture key,
two scratch SQLite databases, and short-lived forked Python processes.
It is never connected to live Omnigent, Herdr, a provider endpoint,
a real repository, or a persistent agent service.

| Physical host | Signed receipt restart | Pending delivery fence | Rollback negative controls |
| --- | --- | --- | --- |
| x1-370 | PASS | PASS | PASS |
| Lenovo | PASS | PASS | PASS |
| Destroyer | PASS | PASS | PASS |

The probe verifies that an unlogged lease debit is detectable, signed
commit and delivery survive cooperative restarts, stale writers and
second commits while pending are denied, and a rolled-back lease DB
mismatches a newer retained receipt record. An older valid receipt DB
fails against a newer anchor, **but passes with the old anchor**.

The refreshed **13-source Lenovo same-host receipt** includes
`worker_receipt_fixture`. This research gate passes only if the
negative vulnerability witnesses are reproduced and the reported
independent custody, source authority, atomic commit, model egress
and agent dispatch flags are explicitly **false**. A green synthetic
fixture does not authorize runtime admission.

At the last physical refresh, Lenovo's available RAM was approximately
**7.75 GiB** (<8 GiB requirement) and Ubuntu AppArmor still blocked
strict Bubblewrap. The requested free OpenCode model, real authenticated
broker, owner-bound quota, safe disposable execution and independent
review remain unverified. Destroyer lacks spare memory/swap.

## Smallest safe next task

Add an independently administered, **out-of-band pinned worker signing
root and monotonic external receipt/witness anchor**. Require a separately
controlled resource-side transaction ID and proof that the worker's
signed commit corresponds to the exact durable lease DB effect.
Test the crash window between debit, signed commit, external witness
publication, observer ACK, delivery receipt and acknowledgement receipt
fan-out—each edge must fail closed without replay or synthetic
success claims. Until independent custody and signed acknowledgements
are established, this stays a draft research artifact.

**No security policies changed, no model/provider calls made, no real
agent task dispatched, no production branch merged, and no existing
fleet workloads stopped.**
