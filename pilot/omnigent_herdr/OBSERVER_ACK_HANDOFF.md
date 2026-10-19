# Herdr/Omnigent observer ACK gate — research checkpoint

**2026-10-19. Research-only, DENY-ONLY; production HOLD.**

## Why this exists

Earlier independent Ed25519 signed-checkpoint tests could identify
rollback against a trusted *newer* checkpoint, but the synthetic broker
could record a receipt without requiring a fresh observer acknowledgement.
This work adds a two-stage handshake and explicitly distinguishes an
SQLite transaction commit from an independently acknowledged effect.

The new code is in:
- `observer_ack_protocol.py`: ephemeral Ed25519 observer, signed
  preparation, state-linked checkpoint and signed commit acknowledgement,
  and a conservative synthetic receipt adapter for local tests.
- `ack_remote_worker_fixture.py`: disposable Lenovo synthetic worker,
  with a private SQLite DB and in-RAM HMAC signing key.
- `two_node_ack_probe.py`: direct bounded SSH experiment on x1-370,
  holding the observer Ed25519 private key solely in x1-370 process RAM.
- `test_observer_ack_protocol.py` and
  `test_two_node_ack_probe.py`: positive, failure and replay tests.

### The deliberate two-stage sequence

1. The worker creates a fresh, private SQLite fixture and a signed
   SYNTHETIC receipt token. Its initial state digest is transferred
   to x1-370 over the existing SSH connection; no token or HMAC key
   is transmitted to the observer.
2. x1-370 issues an Ed25519-signed *prepare* record for the exact
   operation ID, request SHA-256, previous state digest, generation
   and preceding checkpoint hash.
3. Lenovo checks the signed preparation and then commits one SQLite
   synthetic-receipt debit. A status of pending acknowledgement is
   **not** a successful action.
4. The x1 observer receives a bounded SQLite snapshot, checks its
   schema and digest, and signs a new checkpoint and a corresponding
   commit-ACK record. The test's worker identity/snapshot source is
   assumed to come from its SSH fixture, **not independently attested**.
5. Lenovo verifies both the signed checkpoint against its current
   SQLite state and the operation-bound signed ACK. Only then can it
   return `synthetic_receipt_only` with
   `observer_acknowledged=true`.
6. If the ACK is lost or modified, the fixture returns `denied`,
   and the observer remains fenced against another operation.
   **The SQLite debit may already be committed.** Do not retry,
   refund, or replay it automatically without a separately trusted
   reconciliation decision.

All responses, including initial status and denials, explicitly set
`agent_dispatch_authorized=false`,
`model_egress_authorized=false` and
`provider_requests_forwarded=0`. There is no model client,
provider endpoint, real repository, remote push, or production service.

### Physical Fleet Commander results

The three cross-node experiments below all passed with the observer
on x1-370 and the worker on Lenovo:

| Scenario | Worker action | Worker result | Observer result |
| --- | --- | --- | --- |
| Valid, signed checkpoint and ACK | One synthetic debit | Synthetic receipt accepted, **not agent dispatch** | ACK confirmed |
| ACK missing after commit | Debit already recorded | **DENIED**, no synthetic success reported | Remains fenced |
| Signed ACK payload altered after signing | Debit already recorded | **DENIED**, signature rejected | Remains fenced |

Both fixture processes exited and cleaned their temporary state.
The observer private key was not sent to Lenovo; the worker HMAC key
was not sent to x1-370. Actual credentials and model APIs were not used.
All tested receipt paths refuse AI/model dispatch.

### The unresolved distributed transaction problem

A separately signed checkpoint is not proof that every accepted state
change was durably acknowledged. These experiments **do not** establish:
- Crash-atomic SQLite commit plus observer publication. A worker can
  commit and crash before receiving the observer's ACK.
- Persistent observer fence/high-water across its own restart. The
  signer, completed-operation IDs and generations are ephemeral.
- Independent administrative custody. Both nodes are reached using the
  same fleet operator SSH trust context.
- A separately pinned worker-side observer trust root. The *test*
  installs an ephemeral observer public key over the same SSH fixture.
- Independently attested source-authoritative SQLite observations.
  The observer parses the synthetic snapshot delivered by Lenovo,
  but cannot prove it came from the live authoritative storage device.
- Deadline, partition, byzantine rollback, double-signing protection,
  durable revocation, independent published checkpoints and signed
  auditable receipt fan-out.
- Network provider egress, per-user admission, payment ceilings,
  authenticated model pin acceptance or reviewer independence.

The adapter and two-node program are **not** connected to the live
Hermes, Sophia, AssistX, Auto-Router, OpenCode or production agents.
Do not interpret the successful research ACK as runtime authorization.

## Hard next gates

1. Separate control of the observer signer and a persistent protected
   public-key fingerprint; pin this on the resource side outside the
   untrusted worker-controlled channel.
2. Append-only, independently administered observer high-water and
   checkpoint publication; signed duplicate/refusal and revocation
   witnesses; crash-injection testing at every prepare/commit/ACK edge.
3. Independently verify the exact worker DB transaction effect and
   owner/session/worktree authority before publishing a checkpoint.
4. Implement deny-by-default resumption and reconciliation when an
   effect committed but its ACK was lost, with no automatic double debit.
5. Obtain explicit admin approval for Lenovo's Ubuntu vendor
   `bwrap-userns-restrict` profile; repeat strict sandbox tests while
   keeping the global AppArmor protection enabled.
6. Re-qualify Lenovo resources and real model catalog, subscriptions,
   free-tier quotas and bounded egress before one capped disposable
   agent task with independent Codex review.

**Release decision: HOLD. No system security policy changed, no real AI
requests issued, no physical production agent launched.**


## Follow-on: persistent local observer fence fixture

`durable_observer_journal.py` now persists the signed preparation,
observer generation, checkpoint and signed-but-unconfirmed ACK to
separate owner-private research SQLite storage. Physical restart tests
pass on x1-370, Lenovo and Destroyer. An uncertain transaction remains
fenced after restart, and altered ACKs cannot release it.

A cooperative local `confirm_delivery` test can advance the fixture
state when handed the exact signed ACK and a caller-supplied matching
worker digest. It does **not** prove that a real worker received the ACK,
that a second authority approved the delivery, or that the journal
cannot be rolled back. The older valid journal DB restore attack
remains reproducible. See
[DURABLE_OBSERVER_JOURNAL_REVIEW.md](DURABLE_OBSERVER_JOURNAL_REVIEW.md).


## Follow-on: independently verifiable receipt *format* (not authority)

The Lenovo disposable worker now generates its own ephemeral
Ed25519 signer (not the x1 observer signer) and signs both the
post-commit synthetic SQLite state and the later acceptance of the
observer's ACK/checkpoint. The observer rejects a forged commit
**before** signing a checkpoint, and it cannot mark ACK delivery
confirmed without a matching signed worker-delivery receipt.
Five cross-node scenarios passed, including forged worker commit and
delivery receipts. This eliminates reliance on a bare success status
string within the cooperative test.

**Remaining:** the worker's key is self-advertised over the same SSH
session, worker state remains self-reported, and there is no durable,
independently administered signer, identity or monotonic journal.
Signature validation does not prove resource-side authority. Details:
[WORKER_SIGNED_ACCEPTANCE_REVIEW.md](WORKER_SIGNED_ACCEPTANCE_REVIEW.md).
