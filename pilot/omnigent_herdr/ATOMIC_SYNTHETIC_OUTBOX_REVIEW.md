# V2 atomic synthetic lease debit and outbox intent — Fleet Commander review

**Research checkpoint, October 19, 2026. PR draft; no agents or models admitted.**

## Why this slice exists

The existing V1 lease DB and signed worker receipt journal are **two
separate SQLite databases**. Fleet Commander repeatedly demonstrated
a real local transaction gap: the synthetic lease debit can commit,
but its distinct signed receipt journal can remain unchanged if the
process crashes before recording the receipt. On restart, the system
cannot safely infer whether retrying or refunding is appropriate.

This slice introduces an explicitly opt-in **V2 single-database**
alternative that commits the lease debit and a durable, HMAC-authenticated
*unsigned outbox intent* in the SAME SQLite `BEGIN IMMEDIATE`
transaction. This reduces one local crash ambiguity. It does not solve
the cross-node observer publication or worker ACK-delivery gaps.

## Contract and isolation

New `atomic_synthetic_lease_outbox.py` subclasses the existing
research-only `DurableSyntheticLeaseGate`. In
`durable_lease_store.py`, schema version 1 remains the untouched
legacy default. Version 2 is deliberately selected only by the new
adapter. The V1 adapter rejects V2 databases and the V2 adapter rejects
V1 databases; no automatic migration, in-place production upgrade,
or fallback to a V1 debit can occur.

A V2 check **requires** a syntactically valid 128-bit hexadecimal
operation ID. The same operation ID cannot be debited twice. Only a
single synthetic lease debit is permitted per atomic transaction.
Its outbox record binds:

- Contiguous outbox record number and previous-record SHA-256.
- The operation ID, lease JWT ID (not plaintext bearer token), and
  SHA-256 commitment of the exact synthetic request.
- State digests immediately before and after the debit, including
  metadata clock and lease high-water.
- Epoch, strictly increasing lease sequence and bounded receipt count.
- Domain-separated SHA-256 and HMAC-SHA-256, under the separately
  supplied ephemeral research lease signing secret.

A single SQLite transaction updates the lease's `used` and `last_seq`
**and** inserts its outbox intent. Any outbox error rolls back the
debit; SQLite commit precedes the caller's synthetic response.
The state verifier checks the entire outbox chain and the current
lease table/metadata digest on every cooperative read and write.
An outbox insert failure cannot silently leave a committed debit.

To keep this pilot fail-closed, V2 denies unrelated state-changing
operations once an outbox event exists unless a corresponding outbox
transition exists. New mint, epoch rotation, and revocation behavior
are NOT part of this constrained V2 contract. Their implementation
would require explicit outbox event types and negative tests, not a
hidden bypass. The legacy V1 path retains its original behavior.
Malformed/denied V2 checks do not mutate the lease clock.

The V2 inspection endpoint requires a caller-provided EXACT
`(record_no, outbox_record_sha256)` head. A stale head is refused.
It returns `decision=HOLD`,
`agent_dispatch_authorized=false`,
`model_egress_authorized=false`, and
`provider_requests_forwarded=0`.

## Failure injection

The new `test_atomic_synthetic_lease_outbox.py` and
`atomic_synthetic_lease_outbox_probe.py` verify two different
SQLite transaction aborts: raising before outbox insertion and
raising *after* insertion but before the transaction returns.
**Both faults roll back the debit and outbox record.**
The unchanged state remains usable for a later explicitly authorized
synthetic fixture operation. Tests also check duplicate operation
IDs, two state-linked receipts, process restart, wrong signer,
wrong schema, malformed operation IDs, concurrent same-operation
writers, deleted/forged HMAC records, stale heads and manually
altered lease high-water.

The physical probe uses one private scratch directory, fresh in-RAM
HMAC signing secret, and disposable child processes. No real
repository, user token, production DB, model endpoint, network
provider, or agent runtime is used.

| Fleet host | Focused unit tests | Before/after-insert faults | Restart / rollback negatives |
| --- | --- | --- | --- |
| x1-370 | PASS | PASS | PASS |
| Lenovo | PASS | PASS | PASS |
| Destroyer | PASS | PASS | PASS |

The old valid V2 database image is **rejected** when the caller retains
a trusted newer head, but an old valid image **with its old head** still
appears valid. This is a required negative control: no independent
monotonic anchor has been deployed.

## Scope of the word "atomic"

**Established in the cooperative synthetic fixture:** the *local V2
SQLite debit and the keyed unsigned outbox intent* live in the same
transaction, including rollback under injected failures.

**NOT established:** Ed25519 signature creation or publication from
the outbox to a separately custodied observer, receipt fan-out over
network partitions, durable observer ACK, independent worker identity,
machine-attested source authority, or anti-rollback when an adversary
controls both DB and supplied head.

Even with this V2 local guarantee, a worker can commit debit+intent
and then crash before a separate observer signs or publishes a
checkpoint. A later observer ACK can likewise be lost. Those effects
must remain **fenced** pending independent source-authoritative
reconciliation. The HMAC outbox under a worker-held secret is not a
trusted third-party witness and is not permission for agent dispatch.

The experimental V2 implementation is NOT wired into live Herdr,
Omnigent, AssistX, Auto-Router, OpenCode, or any production system.

## Fleet Commander admission and next steps

`fleet_receipt.py` now has **14 same-host evidence categories**.
The new `atomic_outbox_fixture` can be green only if both failure
injection controls and the old-head rollback negative control pass,
while explicitly forbidding claimed external anchor, real source
authority, observer publication atomicity, model traffic or agent
dispatch.

Lenovo's refreshed 14-source receipt is still **HOLD**. Only
`preflight`, `durability_fixture`, `observer_fixture`,
`observer_journal_fixture`, `worker_receipt_fixture` and
`atomic_outbox_fixture` pass. Memory is about **7.75 GiB available**,
below the 8 GiB prefilter. Strict Bubblewrap/AppArmor profile,
model catalog, network deny-only broker, isolated Git, integrated
canary and sandboxed lease are still unresolved. Destroyer remains
resource-ineligible; its scratch research files were cleaned up.

**Smallest safe next engineering step:** a separate authorized, pinned
worker signer and independently custodied observer trust/high-water
publisher that can consume and acknowledge a V2 outbox record,
including durable deduplication, signed resource-side receipts,
and explicit crash/partition tests across commit→sign→publish→ACK.
Never treat one local HMAC record or a successful fixture test as
permission to call a real provider.

**Release decision: DRAFT / NO-MERGE / NO-DEPLOY / NO DISPATCH.**
No host security settings modified; no processes outside the
disposable fixture were stopped.


## Follow-on (October 20): cross-host V2 signature binding experiment

A bounded x1-370 observer / Lenovo disposable-worker fixture now signs
one exported V2 outbox head, verifies an operation-bound observer ACK
and signs a worker delivery receipt. Five physical success/negative
scenarios passed. This proves scoped signature verification across
cooperative processes, **not durable publication**: keys and trust
pins are ephemeral over the same test channel, no outside source
attestation exists and there is no persistent uncertainty ledger.
See [V2_OUTBOX_PUBLICATION_HANDOFF.md](V2_OUTBOX_PUBLICATION_HANDOFF.md).
