# Synthetic durable observer journal — restart/fence qualification

**Research checkpoint: October 19, 2026. Production agent dispatch: HOLD.**

This slice follows the signed prepare/commit-ACK two-node fixture. The earlier
`EphemeralObserver` remembered its `pending`, `fenced` and latest checkpoint
only in RAM. If its process restarted after a worker SQLite debit but before
ACK delivery, it could not reconstruct which operation was uncertain.

## What changed

`durable_observer_journal.py` creates a new owner-private, local SQLite
journal for **synthetic** observer state. It is deliberately separate from
`durable_lease_store.py` (the worker's lease DB). Journal entries persist:

- Exact synthetic operation ID and request SHA-256 (not request plaintext).
- Ed25519-signed prepare and current observer checkpoint.
- Previous and post-commit SQLite *state digests*, signed checkpoint,
  operation-bound signed ACK and acknowledgement lifecycle.
- A coarse fence state: `prepared`, `signed_unconfirmed`,
  `confirmed`, or `aborted`.

Every cooperative reopen verifies the DB's application identity, schema,
owner-only path and file mode, public-signing-key fingerprint, Ed25519
signatures, monotonic checkpoint generations, and the signed checkpoint
chain. It rejects missing or corrupted committed events, missing metadata,
wrong signer, symlinks/hardlinks, unexpected file permissions, and attempts
to silently recreate a missing journal.

The observer signs the new checkpoint and matching ACK **within one local
SQLite transaction**, recording `signed_unconfirmed` before returning
the ACK to the test caller. After restart, a pending prepare or signed but
unconfirmed ACK **remains fenced** until a separate action resolves it.
The source code has no live model client, agent launcher, production Git
access, or provider communication.

### Important limit on confirmation

`confirm_delivery` represents a **cooperative test fixture**. It verifies
the exact signed ACK and caller-supplied synthetic worker state digest,
but has no independently authenticated, resource-side receipt proving
the worker actually received and accepted it. Passing
`correct_ack_can_confirm_after_restart` demonstrates deterministic local
state transitions **not production reconciliation authority**.
The prototype deliberately has no automatic refund, retry, reissue,
forced unfence, or inference dispatch.

A PREPARE can be aborted only if supplied external observation claims the
worker state is still unchanged. Even that observation is not independently
attested by this fixture. An ACK already signed with a changed worker state
cannot be converted back to an unused preparation.

## Mandatory negative controls

The tests reproduce a **successful full-journal rollback attack**:
restoring an older, internally valid observer SQLite file rewinds the
observer's local generation, signed checkpoint and fence state.
A local signing-key match, `synchronous=FULL`, SQLite integrity checks
and signature verification **do not** detect this rollback.

The next production gate must add a separately controlled monotonic
observer checkpoint/high-water mechanism that survives and detects
stale database restores. An operator-controlled x1 signing key alone is
not independent if the worker and observer share administrative custody.

### Physical Fleet Commander measurements

`durable_observer_journal_probe.py` ran disposable short-lived observer
processes on all three research machines. Every process received an
ephemeral in-RAM Ed25519 signer from its fixture supervisor; no private
key was persisted. Results:

| Node | Restart fence and ACK test | Tampered ACK | Old valid DB restore | Agent admission |
| --- | --- | --- | --- | --- |
| x1-370 | PASS | DENIED | Vulnerability reproduced | HOLD |
| Lenovo | PASS | DENIED | Vulnerability reproduced | HOLD |
| Destroyer | PASS | DENIED | Vulnerability reproduced | HOLD |

The experiment checks preparation fencing after restart, a safe abort only
for unchanged synthetic state, signed-unconfirmed ACK surviving restart,
malformed ACK refusal, cooperative exact-ACK confirmation, and continued
operation only after that fixture confirmation. Old-image restore remains
vulnerable on each host. All fixture directories are temporary.
No long-running observer or agent process remains installed.

## Admission accounting

`fleet_receipt.py` includes a **twelfth**, Lenovo-host-labeled,
`observer_journal_fixture` evidence file. The new synthetic gate is
green **only** if the restart/fencing checks pass *and* the rollback
vulnerability is reproduced, the signer was not saved, no claimed external
high-water or independent signing custody exists, and dispatch and provider
forwarding remain false. This is a research result, not an admission.

Latest Lenovo physical 12-source result:

- PASS: installed preflight, SQLite lease restart fixture, Ed25519
  checkpoint fixture, and local observer journal restart fixture.
- FAIL: resource prefilter (~7.78 GiB available, below 8 GiB requirement),
  strict namespace sandbox, AppArmor activation, complete requested
  model catalog, deny-only socket under sandbox, isolated Git canary,
  integrated same-session canary, and sandboxed lease probe.
- `decision=HOLD`, `agent_dispatch_authorized=false`.

Two-node Ed25519 signing and earlier ACK experiments are still separate
from these **same-host** gates. Their successes do not establish distinct
administrator custody or change Lenovo's sandbox policy.

## Smallest safe next step

1. Implement a **separately administered append-only observer high-water
   publisher** with pinned signer trust roots and monotonic chain
   verification. Never use the worker DB or same mutable journal as
   the sole latest-generation authority.
2. Require authenticated **resource-side acceptance / delivery receipts**
   tied to the exact worker SQLite transaction, before any observer fence
   can be released. Include crash injection at commit, checkpoint durable
   write, ACK transit, and confirmation boundaries.
3. Explicitly prove the observer cannot be rolled back using the same
   operator credentials or machine storage image. If independent custody
   is unavailable, keep the deployment NO-GO.
4. Obtain prior administrator authorization to install the reviewed
   Ubuntu `bwrap-userns-restrict` profile on Lenovo; do not disable the
   global user-namespace restriction.
5. Requalify Lenovo capacity, provider authentication, exact model pins,
   free tier quotas, one disposable real coding worktree, and separate
   Codex reviewer evidence only after earlier gates pass.

**No live agents, hosted model requests, policy changes, production
deploys, protected branch merges or existing-workload terminations
were performed in this research slice.**


## Later separation experiment: cooperative witness log

The separate `checkpoint_witness_log.py` prototype publishes signed,
sequential state checkpoints to a different append-only-by-convention
file. It can identify an old worker or witness image **only when**
the caller retains a trusted newer anchor outside the rolled-back file.
Physical x1-370/Lenovo tests pass the synthetic negative controls;
independent administrative custody, durable anchor publication, and
resource-side ACK authenticity remain unproven. See
[CHECKPOINT_WITNESS_REVIEW.md](CHECKPOINT_WITNESS_REVIEW.md).
