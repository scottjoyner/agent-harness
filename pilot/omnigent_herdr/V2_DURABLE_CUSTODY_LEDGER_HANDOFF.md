# V2 custodian signing ledger — persisted local sequence, revocation, rollback

**Fleet Commander research checkpoint — October 20, 2026**
**Release decision: HOLD / PR DRAFT / NO-MERGE / NO LIVE AGENTS / NO INFERENCE**

## Narrow milestone

The previous three-node synthetic experiment (x1-370 observer,
Destroyer witness signer, Lenovo custodian signer) verified four
distinct Ed25519 cryptographic roles, exact witness receipts, and a
generation-chained custody checkpoint. But Lenovo's disposable
`sign` command was stateless: the caller supplied generation,
previous checkpoint digest, and witness head. It could re-sign
identical or contradictory requests after a restarted SSH session,
and it had no local durable revocation state.

This slice adds **`v2_custody_signing_ledger.py`**, a bounded,
owner-private SQLite signing journal **only within the disposable
Lenovo research root**. It does not install a daemon, model router,
provider adapter, production signer key or source-authoritative
transaction worker.

## Ledger behavior

- Bootstrapping `v2_custody_signer_fixture.py` exclusively generates
  its 0600 ephemeral Ed25519 signer and creates a separate private
  SQLite file with a distinct application ID/version. Any attempt
  to bootstrap again fails instead of overwriting the key or ledger.
- The first accepted `sign` binds a fixed fixture scope and the
  witness/observer public keys, worker public-key fingerprint,
  observer/worker identities and custodian signing-key fingerprint
  in SQLite. **These roots originate from the test caller**; saving
  them is not external independent trust.
- Subsequent `sign` calls must use the next sequential generation,
  the exact last signed checkpoint SHA-256, strictly increasing
  witness record count and observer revision, the exact same
  pinned role configuration and no decreasing expiration.
- The signer verifies the witness-signed receipt and creates a
  synthetic Ed25519 custody checkpoint, then stores the *original
  bounded request* (including the witness-signed receipt), signed
  checkpoint and SHA-256 within **one `BEGIN IMMEDIATE` SQLite
  transaction** under a full synchronous journal. It returns the
  signed artifact only after the commit is successful.
- Every subsequent read/sign/revoke verifies the **complete stored
  chain**, including signed checkpoint signatures, witness receipt
  signatures, fixed identities, exact digest links, witness record
  advancement, bounded canonical JSON and the local tombstone.
  Corrupted, deleted, relinked, world-readable or wrong-schema
  ledger files fail closed.
- The original `sign` is now **non-idempotent**: a repeated
  generation is rejected even with identical request bytes.
  A lost response must use `readback` with the exact latest
  generation, witness head, observer head and scope. Readback
  returns the already stored, signature-verified checkpoint with
  **zero new signing/ledger mutations**. Readback of older
  generations is refused after a newer checkpoint has been stored.
- `revoke` requires the exact latest generation and checkpoint
  SHA-256. It inserts a durable **local** terminal tombstone.
  Later readback, sign, and duplicate revoke calls all fail, even
  when performed by fresh SSH/Python processes. This is a
  *local* tombstone, NOT externally signed/irreversible revocation.
- Neither the key nor SQLite journal leaves the isolated scratch
  directory, and no user repository or production data is copied.

**Important security boundary:** SQLite atomicity applies to the
custodian's **local** checkpoint/history transaction only.
The separate worker debit/outbox, observer ACK, Destroyer witness
append, Lenovo signature and client receipt confirmation are NOT
distributed atomic events. A lost response never permits an
automatic debit retry, refund, new signing operation or unfence.

## Test evidence

The local unit tests exercise signer restarts, sequential generation
2, duplicate/parallel sign races (one succeeds), stale head,
wrong roots/scope, altered witness receipt or signed checkpoint,
corrupted SQLite history, symlink/hardlink/permissive inode,
injected mid-transaction failure and persistent local revocation.

**Physical Fleet Commander (three nodes):**

| Scenario | Actual result |
| --- | --- |
| x1 synthetic V2 worker/observer + Destroyer signed witness + Lenovo signed custody checkpoint | PASS |
| First checkpoint committed; fresh Lenovo SSH/Python process readback | Exact same stored signature, PASS |
| Duplicate `sign` for already stored checkpoint | DENIED |
| Generation 2 signed with exact generation 1 digest; full history reopened | PASS |
| **Actual older Lenovo SQLite image restored** with newer expected head | DENIED |
| Same older valid ledger with its **old head** | **ACCEPTED — expected security counterexample** |
| Local revocation committed; fresh process attempts readback | DENIED |
| After revocation, an otherwise valid NEW worker-delivery/witness checkpoint is sent to `sign` | DENIED |
| Same administrator restores old valid, **unrevoked** Lenovo SQLite image | **Revocation undone — expected security counterexample** |
| Revoked image restored after negative test | Future signing/readback still DENIED |
| Model/provider calls, agent dispatch | ZERO / NEVER AUTHORIZED |

**Verified:** 561/561 full pilot tests on x1-370; **81 focused tests
on Lenovo**; **25 focused witness tests on Destroyer**, followed by
a successful actual x1→Destroyer→Lenovo physical ledger/revocation
probe. All remote signing keys and trial SQLite files lived solely
inside the disposable root and were cleaned using EXIT traps.

A fresh process is **not a full machine reboot**. No persistent
custodian service or trust root was installed. The tests do not prove
hardware crash recovery, independent key custody, TPM-backed
counters or immutable external high-water.

## Existing NO-GO requirements remain

1. **Independent authority**: Lenovo, Destroyer and x1-370 are all
   accessible through the SAME operator credentials. Neither
   independently pinned roots nor a separately administered
   signed witness policy exists.
2. **Rollback resistance and revocation**: restore the old SQLite
   image *and* expected old trust head and the signer accepts the
   older valid history. A shared administrator can undo a locally
   recorded revocation. The test explicitly reproduces this.
   External immutable monotonic counters / independently controlled
   checkpoint authorities are missing.
3. **Resource-side attestation**: the V2 worker can sign claims
   about its own synthetic SQLite transaction. No independently
   witnessed physical resource-side debit ID or real provider/agent
   action is proven.
4. **Partition/crash effects**: local journals do not prove
   crash-safe exactly-once effects across x1, Destroyer, Lenovo,
   or disconnected partitions. No automatic retry/refund/unfence.
5. **Production admission**: the separate **15-source Lenovo same-host
   fleet receipt remains HOLD**. The global AppArmor user namespace
   restriction was NOT weakened. Vendor profile admin approval,
   strict sandbox qualification, >=8 GiB available memory,
   real approved model pins/quotas, secure egress and owner-bound
   runtime receipts remain unresolved. Destroyer lacks enough
   free RAM/swap for multi-agent workloads.

**Smallest next safety validation:** independently administered
witness/revocation custody outside shared fleet access, followed by
persistent external monotonic high-water and source-side signed
transaction IDs, then crash/replay/partition injection. Until
that independent authority is demonstrated, this implementation
is only a synthetic research gate: no agent runtime, inference,
production deployment, public tunnel, merge or host security change.


## Follow-on: signed off-ledger revocation observed after Lenovo rollback

An additional **research-only fifth-role signing fixture** on x1-370
now issues signed `checkpoint_recorded` and `checkpoint_revoked`
authority states tied to the exact Lenovo custody checkpoint.
After a physical restore of an older, **unrevoked** Lenovo SQLite
image, the off-ledger, retained newer signed authority revocation
still **rejects the old checkpoint as valid evidence**.

This is conditional on externally retained, trustworthy pins,
expected signed-state SHA-256 and sequence floor. The same operator
controls all of these values in this experiment. Restoring the
old authority state and its old head/floor makes the old checkpoint
look valid again: independent immutable custody remains UNPROVEN.
See [V2_EXTERNAL_REVOCATION_AUTHORITY_HANDOFF.md](V2_EXTERNAL_REVOCATION_AUTHORITY_HANDOFF.md).
