# Synthetic resource-side commit and ACK receipt — research review

**Fleet Commander checkpoint, 2026-10-19. Draft/NO-GO.**

This research slice moves beyond trusting Lenovo's unsigned statement
that a synthetic SQLite receipt was committed or an observer ACK was
received. The worker now generates a disposable Ed25519 signing key
**in its own process RAM**, distinct from the x1-370 observer signer,
and signs both of those protocol transitions.

## Contract

`worker_acceptance_receipt.py` signs two distinct, domain-separated,
canonical-json Ed25519 message kinds:

- `synthetic_worker_commit`: declares the exact worker and observer role,
  operation ID, SHA-256 of request and signed preparation, before and
  after SQLite state digests, generation, sequence, receipt number, and
  **false** agent dispatch / zero forwarded provider requests.
- `synthetic_worker_delivery`: includes everything in the commit
  statement **plus the exact observer signed ACK hash and signed
  checkpoint hash**. This is emitted only after the worker verifies both
  the observer checkpoint against its current SQLite state and the
  matching operation-bound ACK.

The observer validates both signatures against the worker's ephemeral
public key; validation requires an exact match of **every** message
claim, strict types, distinct observer and worker signer roles,
canonical signature encoding and separate Ed25519 domain. The observer
does not confirm its ACK delivery on receipt of a mere status string:
the matching signed **worker delivery receipt** is mandatory.

The worker's public key is introduced through the same strict-host-key
SSH *research channel* and pinned for that test session only. Thus,
**worker signing identity is self-declared and not independently
attested**. Neither its signing key nor its receipt state is
restart-durable. A stolen/compromised worker signing key could lie
about its own SQLite state. The copied SQLite snapshot still comes
from the same Lenovo fixture and does **not** prove what real storage
hardware executed. This is a stronger typed/cryptographic binding
between cooperating research processes, **not source authority**.

## Physical Fleet Commander test results

The physical fixture was exercised between x1-370 (observer) and Lenovo
(disposable SQLite worker) with five outcomes:

| Scenario | Expected and observed result |
| --- | --- |
| Valid worker commit + signed observer ACK/checkpoint + signed worker delivery | PASS; synthetic receipt only; observer unfenced after validated delivery |
| Observer ACK missing after SQLite debit | PASS negative; worker denies and observer remains fenced |
| Observer ACK payload modified after signing | PASS negative; worker denies and observer remains fenced |
| Worker commit payload modified after signing | PASS negative; observer rejects before publishing checkpoint; pending prepare remains unresolved |
| Worker delivery payload modified after signing | PASS negative; observer rejects and remains fenced, even if worker committed and accepted observer ACK |

`test_worker_acceptance_receipt.py` passed on x1-370, Lenovo and
Destroyer. On Destroyer an initial test import failed because one
dependent module was omitted from the temporary Python fixture; after
the missing module was copied, **all 20 focused tests passed** and
the temporary files were deleted. This was a fixture setup issue,
not a failure of signature verification.

Unit tests also reject signature substitutions, incorrect operation IDs,
wrong request/prepare/state digests, other user/key roles, replayed
sequence, Boolean-as-integer coercion, extra/missing claim fields,
bad signer fingerprints and any claim that a provider was contacted
or an agent was authorized.

No provider requests, AI agent execution, real repository writes,
upgrades, fleet workload stops or production system changes occurred.

## New blocker details

The physical results **do not** establish:
- An independently pinned, durable worker signer public key and
  separately administered revocation/rotation and signer key custody.
- An independently authenticated source-of-truth DB snapshot or
  resource-side transaction ID that a worker cannot forge.
- A persistent, cross-restart worker delivery receipt store and
  anti-replay nonce ledger; both signing keys are disposable.
- Atomic commit/ACK publication across Lenovo and x1-370.
  A worker may commit and lose its ACK; the observer stays fenced.
- Trusted continuous checkpoint publication, independently retained
  latest high-water, distinct administrator roles, or hard anti-rollback.
- Real model availability, free-tier quota/charges, owner-bound
  agent runtime credentials, or Git checkout admission.

The cross-node output is **not** inserted into Lenovo's same-host
12-source qualification receipt. Lenovo still fails strict Bubblewrap
due to the unchanged Ubuntu AppArmor policy and recently lacked the
8-GiB MemAvailable prefilter. Destroyer remains resource-ineligible.

### Next smallest safe test

Bind each synthetic worker-signed commit/delivery pair to a separately
administered, **durable** observer log (including an exact operation
nonce and worker fingerprint). Prove replay, lost-ACK, restart,
signature/key-rotation and stale-effect negative cases with a pinned
trust root supplied independently of the untrusted worker connection.
Do not unfreeze the observer solely on a worker-generated statement.

**All physical research receipts explicitly set**
`agent_dispatch_authorized=false`,
`provider_requests_forwarded=0`. Draft PR stays **HOLD**.


## Follow-on: local restart-durable signed receipt journal

`durable_worker_receipts.py` now persists the disposable worker's
signed commit and ACK-delivery statements in a separate SQLite
journal with exact-head, hash-chain and key continuity checks.
Physical subprocess/restart tests pass on x1-370, Lenovo and
Destroyer. The journal is *not* atomically coupled to the lease DB:
an accepted debit can exist before its signed commit is written.
An older DB paired with an old anchor remains a rollback attack.
These are explicit HOLD conditions, not grounds for automatic
reconciliation. See
[DURABLE_WORKER_RECEIPT_REVIEW.md](DURABLE_WORKER_RECEIPT_REVIEW.md).
