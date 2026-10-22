# V2 worker/source crash-window and lost-response recovery — research

**Fleet Commander research checkpoint — October 20, 2026**
**Release decision: DRAFT PR / HOLD / NO MERGE / NO MODEL OR AGENT DISPATCH.**

## The unresolved cross-host problem

In V2, the synthetic worker on x1-370 commits a lease debit plus
authenticated outbox intent to *worker-local* SQLite, while the
Destroyer source commits a separate **synthetic** balance debit plus
Ed25519-signed receipt to *source-local* SQLite. Each side can prove
its own local atomicity, but the pair is **not** one distributed
atomic transaction.

If the worker receives no answer from Destroyer, the source may have
committed and lost the response, committed and then become
unreachable, or never committed at all. The absence of a reply—or
a read-only lookup that finds no record—**cannot authorize another
debit, refund the worker, release a fence, or dispatch an agent.**

This slice makes those distinctions testable without granting new
runtime authority.

## Source-owned read-only reconciliation

`SyntheticResourceSource.recover_committed_operation` in
`v2_source_transaction_fixture.py` adds a bounded, logically
read-only query. The caller supplies:

- Exact operation ID and original signed worker-intent SHA-256.
- **Concrete previously trusted source head** (record count and
  receipt SHA-256); this may not be omitted or learned by trusting
  the new reply.

The source reopens and verifies its complete existing SQLite
balance/receipt history. It returns a signed receipt **only when the
latest source record is exactly the next one after the caller-held
prior head**, and its operation ID, request digest and linked
previous-record hash match. It never searches arbitrarily far back
through an evolved source, invents a missing effect, sends a new
commit, consumes another unit, or creates an agent/egress grant.

The `recover` CLI is a new one-shot command. A missing or stale
source record is denied as an evidence query; **this denial is not
proof of nonexecution**.

## Explicit non-authorizing decision classification

`v2_cross_host_uncertainty.py` introduces
`classify_source_recovery`. Given original independently verified
worker-publication context, explicitly supplied public keys/pins
and prior source head, and an optional recovered source reply:

| Decision | Evidence | What it authorizes |
| --- | --- | --- |
| `SOURCE_LOCAL_COMMITTED_HOLD` | Recovered source receipt verifies cryptographically, matches the original signed worker operation and intent, and directly succeeds the caller's prior source head. | **No operation or deployment.** The *source-local synthetic* commit is supported, but worker/source distributed atomicity is not. |
| `UNCERTAIN_HOLD` | No reply, partition, timeout, or source says the requested evidence is unavailable. | **Nothing.** Do not conclude no effect; do not retry, refund or unfence. |
| `QUARANTINED_HOLD` | Malformed evidence, substituted public key, forged signature, wrong operation/head or unsafe self-declared authority. | **Nothing.** Treat evidence as untrusted pending independent review. |

All results set `decision=HOLD`,
`agent_dispatch_authorized=false`,
`model_egress_authorized=false`,
`provider_requests_forwarded=0`,
`automatic_retry_authorized=false`,
`automatic_refund_authorized=false`,
`lease_unfence_authorized=false`, and
`worker_source_distributed_atomicity_verified=false`.

This is a **pure research classifier**: it does not open a
production socket, change any DB, contact a model provider, or
own a trusted execution role.

## Physical fault injection via Fleet Commander

The source one-shot research CLI accepts an explicitly named
`--research-fault` flag **only for its disposable `commit`
operation**:

- `crash-before-source-commit`: exits the current isolated CLI
  process with code 96, before entering the source commit.
- `crash-after-source-commit`: commits the source-local SQLite
  debit/receipt and then exits **that same disposable process**
  with code 97, before writing any success response.

The test harness permits these two exceptional process statuses
only when it deliberately asked for the corresponding injection.
No service, other PID, system network route, AppArmor setting,
real file or external resource is changed.

Actual x1-370 → Destroyer test sequence:

1. x1 commits one synthetic worker lease/outbox intent and signs it.
   Destroyer starts an isolated two-unit synthetic resource with
   its own disposable Ed25519 key and SQLite file.
2. Crash **before** the source commit: no response, read-only
   recovery denied, source genesis verified unchanged. The
   caller's recovery decision remains `UNCERTAIN_HOLD`.
3. Crash **after** source commit but before response: no response,
   source effect committed. A **fresh SSH/Python process** performs
   read-only `recover` with the previously trusted genesis head.
4. x1 validates the recovered receipt's Ed25519 signature,
   source signer pin, operation ID/request SHA-256 and immediate
   source-head succession **together with the independently
   signed worker publication**. Result:
   `SOURCE_LOCAL_COMMITTED_HOLD` (still no runtime grant).
5. Repeated recovery never debits again. A repeated `commit`
   with the already used operation ID is denied. Source balance
   stays 1 unit after the first committed operation.
6. Simulated partition/no readback remains `UNCERTAIN_HOLD`;
   malformed or wrong-key evidence becomes
   `QUARANTINED_HOLD`, not source-success.
7. The prior physical negative controls remain: a second
   *distinct* synthetic debit exhausts the 2-unit budget,
   replay/fabricated receipts are rejected, and **an old valid
   source database is accepted with its old trusted head** even
   though a retained newer head rejects that rollback.

**Verification:** full x1-370 pilot Python suite
**636/636 passed** (612 before this slice); **52/52 focused tests
on Destroyer passed**; complete x1↔Destroyer physical crash-window
canary passed with both exit-code injections and no unexpected
errors.

These tests validate local SQLite transaction behavior and
controlled **process** termination. They do **not** reboot a
physical machine, cut the network, introduce actual power loss,
or prove durability against hardware/storage corruption.

## What remains unproved and blocked

- The source is a fake two-unit counter owned by Destroyer, not
  an independently administered production provider/resource.
  Its public-key pin was bootstrapped over the same fleet SSH
  trust path. Source database and expected trust head are both
  rollbackable by the shared operator.
- A signed source-local receipt plus a signed worker-local
  publication do **not** constitute a globally atomic or
  exactly-once transaction across x1/Destroyer. Late effects,
  partitions, worker crashes, revocation races and true source
  operator separation remain separate tests and approvals.
- `UNCERTAIN_HOLD` cannot safely be resolved by guessing that a
  missing receipt means no debit. No automatic retry, refund,
  compensation, resurrected lease or fence release is implemented.
- No independent immutable monotonic head, trustworthy external
  signer, source-owned *production* transaction ID, trusted
  revocation channel or out-of-band operator custody exists.
- Lenovo's existing **15-source SAME-HOST agent admission receipt
  remains HOLD**: narrow AppArmor vendor profile not approved/
  loaded, ~7.75 GiB available memory below 8 GiB prefilter,
  real model pins/quotas and owner-bound model/provider execution
  still unverified. Destroyer lacks RAM and swap headroom for
  multi-agent inference. Research results here are NOT added
  to the Lenovo admission receipt.

**Smallest next independent milestone:** establish an
independently controlled real resource signer and protected
external high-water; then test signed source-side IDs through
real crash, partition, replay, revocation and split-brain
scenarios. Continue to keep the agent runtime, production
provider routing and deployments disabled.

**DRAFT / NO-MERGE / HOLD / NO LIVE AGENT / NO MODEL FORWARDING.**


## Follow-on: persistent signed-evidence HOLD journal on x1

A new opt-in `v2_uncertainty_journal.py` records the original
worker-signed intent and exact recovered source-signed receipt in
a private, bounded local SQLite hash chain. A new x1 Python
process can re-verify the entire evidence chain and return
`SOURCE_LOCAL_COMMITTED_HOLD` after remote read-only recovery,
but may not retry, refund, unfence, dispatch or route a model.
Actual before- and after-commit source-process failures
and fresh-process journal readback passed in the same physical
x1→Destroyer disposable experiment.

Same-admin restoring both the journal DB and expected trusted
head remains a rollback risk. See
[V2_DURABLE_WORKER_UNCERTAINTY_HANDOFF.md](V2_DURABLE_WORKER_UNCERTAINTY_HANDOFF.md).
