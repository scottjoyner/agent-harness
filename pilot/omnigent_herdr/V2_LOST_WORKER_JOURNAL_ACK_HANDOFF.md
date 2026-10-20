# V2 lost worker-journal acknowledgment — read-only crash recovery

**Fleet Commander checkpoint: October 20, 2026**
**Decision: HOLD / draft PR / no agent execution or production deployment**

## The new crash window

Previous V2 source recovery established two locally atomic but separate
transactions: x1-370's synthetic worker lease/outbox and Destroyer's
independently stored *fake* two-unit resource debit plus its Ed25519-signed
receipt. A restarted worker can query Destroyer for a source receipt after
losing Destroyer's reply, and can persist a fail-closed classification in
`v2_uncertainty_journal.py`.

But that worker-side journal had its own unhandled acknowledgment window:
the journal could commit `SOURCE_LOCAL_COMMITTED_HOLD` locally and lose
its caller response. Blindly replaying the write would collide with an
already advanced expected journal head. Worse, an application could treat
a missing journal ACK as proof that the source action did not occur.

This change provides a narrow, read-only way to resolve the *journal
observation itself* after a lost journal acknowledgment, without
requesting any additional source effect or writing a new observation.

## New `reconcile_lost_observation_reply` contract

The worker-scope journal now accepts four pieces of proof:

- The **original exact source response** (possibly an explicit
  no-response value), never a newly invented success result.
- The **exact previously trusted journal head** (record number plus
  SHA-256) retained by the caller before it attempted to append.
- The immutable original journal genesis/context SHA-256.
- The signed-worker and signed-source evidence already stored in
  the private journal, which is re-verified with fixed signer pins,
  original operation/intent and predecessor hashes on every read.

The method succeeds only if the *latest* observation in the journal
is **exactly one record after** the caller-held previous head, its
stored predecessor is identical, and the stored source response is
exactly the originally supplied response. Reopening verifies the
entire prior journal history and source signatures/identities.

This method uses an SQLite **read transaction only**; it neither
appends nor modifies an observation and never touches the worker
lease/outbox, source debit, observer, witness, network, or provider.

It does NOT attempt to "search history" when the source/journal
state has progressed to some other entry. A missing observation or
a stale/wrong head is denied as evidence. That denial means
**UNCERTAINTY/HOLD**, not "no source effect" and not authorization
to retry/refund/unfence anything.

The research-only one-shot CLI now supports `inspect`, `observe`,
and `reconcile` with exact pinned context/head and bounded stdin.
Only `observe` accepts two opt-in fixture-only failure modes:

| Failure mode | Injected point | Meaning |
| --- | --- | --- |
| `crash-before-journal-commit` | Exit code 96 before any append attempt | No new local observation; previous journal head remains. |
| `crash-after-journal-commit` | Exit code 97 after SQLite commit returns but before acknowledgment | A new observation exists, but the caller saw no response. |

The exits affect **only the specified disposable Python fixture
process**. They are not network partition injection, service
termination, host reboot, or real disk/power-loss testing.
Fault flags on `inspect` or `reconcile` are denied.

All outcomes retain `decision=HOLD`, with no model egress, agent
dispatch, automatic retry, refund, or lease unfence; even a
cryptographically valid source-local transaction does not prove
distributed atomicity.

## Tests and physical Fleet Commander results

New focused tests cover:

- Before-commit process exit leaves no observation or phantom head.
- After-commit lost ACK is reconciled by a **new Python process**
  using the pinned previous head and original source reply.
- Repeated read-only reconciliation yields exactly the same
  signed-evidence HOLD head, never another journal row.
- No local write under injected SQLite `BEFORE INSERT` abort:
  the source receipt can remain signed while the journal
  stays at its previously trusted head.
- Missing journal row, mismatched source receipt, stale head,
  wrong context, duplicate JSON keys, forged/altered persisted
  receipt, fault option on a read-only method, and repeated
  `observe` at the previous head all fail closed.
- Reconciliation never repeats a source-local SQLite debit.
- A recovered valid source receipt and corresponding retained
  journal head remain read-only **HOLD** evidence.

**Verified local:** 677/677 complete pilot Python tests (660 prior
plus 17 new focused tests).

**Verified physical:**
- 93/93 focused source/journal tests PASS on Destroyer in an
  isolated disposable private directory.
- Full x1-370 → Destroyer canary PASS. Destroyer's one-shot fake
  source process was deliberately exited before source commit
  and again after its local commit but before its ACK. x1
  recovered the signed source effect without re-debiting.
- The worker journal then experienced both controlled
  `observe` crash windows: before local append (no journal
  record added) and after local append (record committed, reply
  lost). A **fresh x1 process** reconciled the exact signed
  observation from the previous head and source reply.
- A second journal append at the previous head was denied;
  fresh-process exact-head readback succeeded; stale-head
  readback was denied.
- The source balance was still one unit. All model/agent/
  provider authorization booleans remained false.

Every physical source/journal file, test signing key and temporary
research process is disposable; the remote scratch directory is
cleaned with a bounded `EXIT` trap. No production data/services,
AppArmor controls, user repositories or existing workloads were
modified.

## Unresolved HOLD reasons

1. The worker journal is *locally* durable, not tamper-proof.
   Restoring an old valid worker journal **together with its
   old claimed trusted head** still passes. Same-fleet
   administrator controls all test machines and cryptographic
   trust pins; this is not independent custody.
2. Source and worker SQLite transactions are separate; a
   successful source receipt and a successful worker-journal
   reconciliation do not imply an atomic distributed operation
   or guarantee exactly-once effect across physical partitions.
3. Source is an **artificial two-unit resource**, not a real
   production provider. No independently administered signer,
   source-authoritative real transaction ID, protected external
   monotonic high-water or independent revocation witness exists.
4. The physical experiment exits bounded disposable processes.
   It does **not** prove power-loss durability, disk corruption
   behavior, network partition fencing, key revocation or
   multi-operator custody.
5. The separate Lenovo **15-source SAME-HOST admission receipt
   remains HOLD**, including the narrow AppArmor vendor profile
   approval, 8 GiB memory threshold, authenticated real model/
   provider quota and owner-bound runtime receipt gates.

**Next smallest independent qualification:** establish an
externally administered resource-signer/key and protected
monotonic head, then test physical crash/partition/fencing and
revocation against actual authorized resource transaction IDs.
Do not enable model loading, agent dispatch or automatic retry.

**DRAFT / NO MERGE / NO DEPLOY / NO AGENT / NO PROVIDER FORWARDING.**
