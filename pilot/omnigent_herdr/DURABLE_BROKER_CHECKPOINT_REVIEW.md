# Restart durability, rollback detection, and observer custody — 2026-10-19

**Research-only; all resulting authorization decisions remain HOLD.**

The original `capability_gate.py` kept counters, revocations and the lease
epoch exclusively in process memory. This slice adds a SQLite-backed
experimental alternative and a read-only Ed25519 observer-checkpoint
verifier, but **neither constitutes production replay protection**.

## 1. SQLite restart durability

`durable_lease_store.py` implements `DurableSyntheticLeaseGate` with
a local SQLite state file in an explicitly created, owner-only directory.
It stores issued synthetic lease IDs and token hashes (not token plaintext),
highest accepted sequence, bounded receipt counts, revocation flags,
epoch, a persisted process-clock watermark, and a keyed continuity
commitment. It intentionally persists **no HMAC signing key**.
The caller must independently control that key and inject the same key
across restarted broker processes.

Operational restrictions:
- The DB path must be absolute, owner-owned, regular, non-symlinked,
  single-linked, and in an owner-only directory; newly created DB has
  mode 0600. Reopening with a missing DB **fails closed** rather than
  silently starting a new authority.
- SQLite uses explicit `BEGIN IMMEDIATE` transactions and
  `synchronous=FULL` with a DELETE journal for cooperating concurrent
  processes. Database identity, version, key commitment and
  `PRAGMA integrity_check` are checked.
- Only `schema=1,op=synthetic_receipt` is accepted; response
  `synthetic_receipt_only` still carries
  `agent_dispatch_authorized=false` and
  `provider_requests_forwarded=0`.
- A completed receipt is committed before the simulated acknowledgement.
  Cross-process duplicate sequence submissions serialize to one accepted
  receipt; the next process retains the high-water, quota and revocations.
- The temporary signing key in `durable_restart_probe.py` is held in
  the supervisor's RAM and inherited by short-lived fixture children,
  not placed on disk or in argv, shell history or provider environment.

**Explicit negative finding:** replacing the entire DB file with a valid
older SQLite image makes a previously consumed synthetic receipt
redeemable again. Every physical probe asserts this vulnerability is
reproduced and `file_rollback_protection_verified=false`.
Corruption checks, a local digest, synchronous SQLite, and ordinary file
permissions do **not** provide external anti-rollback authority.
Furthermore, same-UID adversaries and hostile filesystem swaps remain
outside the cooperative-fixture threat model. The local test does not
prove durable behavior through unclean power loss, multi-host partition,
disk replay, a storage-device write cache, or broker ownership compromise.

## 2. Observer checkpoint verifier

`observer_checkpoint.py` opens the SQLite file **read-only**, without
access to the worker HMAC signing key. A signed checkpoint contains:
worker and observer roles, generation number, preceding checkpoint hash,
state digest, timestamp, and a versioned domain-separated Ed25519
signature. Verifier takes an observer public key and pinned SHA-256
fingerprint and an **externally trusted generation floor**.
All signatures and canonical payloads are validated before comparing
the current SQLite state digest.

The signer helper `sign_fixture` generates signed checkpoints for
temporary synthetic tests; **it is not a deployed observer service**.
A valid signature does not prove the signer is operationally independent,
that every accepted state change was reported, or that a supplied
checkpoint is the most recently published checkpoint.

Two required negative controls:
- Restore an old valid DB and compare with the **newer signed checkpoint**:
  the digest differs and the verifier rejects the old state.
- Supply the old DB **and old signed checkpoint** with an **old generation
  floor**: the signature and DB appear valid. With an independently
  supplied newer floor, the stale checkpoint is rejected.

An operator must establish external monotonic/latest-checkpoint custody.
No physical independent approval, permanent pinned signing root,
continuous publication, durable witness ACK, quorum, anti-replay
high-water across trust domains, or source attestation exists yet.
The verifier always sets `rollback_resistance_verified=false` and
`agent_dispatch_authorized=false`.

## 3. Physical Fleet Commander evidence

| Host | SQLite restarted-process proof | Ed25519 fixture | Worker admitted |
| --- | --- | --- | --- |
| Lenovo | **PASS** | **PASS** | **NO** — AppArmor strict namespace blocked |
| Destroyer | **PASS** | **PASS** | **NO** — low RAM/swap and active workloads |

The restarted-process test checks replay, quota, revocation, epoch,
cross-process concurrency, corrupt/missing/wrong-key DB refusal, and
demonstrates the valid-old-image rollback vulnerability.
All fixtures use temporary directories and synthetic, ownerless data.
No real repository, user data, provider API, or production DB was accessed.

### Cross-node experiment: x1-370 observer / Lenovo synthetic worker

`two_node_checkpoint_probe.py` runs on `x1-370` and starts one bounded
SSH worker fixture on Lenovo. The **observer Ed25519 private key remains
in the x1-370 Python process RAM**; the worker generates a separate
random HMAC key in its own Lenovo process RAM. Only synthetic SQLite
snapshot bytes are transported to x1-370. The observer validates their
SQLite schema locally, creates two signed state checkpoints, and rejects
the worker's restored old state against the newer one. It also rejects
the old signed checkpoint when supplied a newer trusted generation floor
and reproduces the stale-floor failure.

**Physical two-node result: PASS for the ephemeral fixture.** This is
physical process/key separation for a temporary test, not durable
independent custody. The observer currently trusts that the SSH fixture
returned the actual SQLite state; it does not verify an independent
resource-side execution receipt. Its latest-generation high-water and
private signing key disappear on process exit, and no checkpoint is
reliably published outside the two-node test. Both machines share the
operator's SSH trust context. Treat a passing two-node test as
**insufficient for production security**.

## 4. Admission and next validation

The existing Fleet Commander receipt has **11 same-host JSON gates**:
preflight, resource, strict sandbox, catalog, AppArmor,
deny-only socket, isolated Git, same-session synthetic canary,
ephemeral lease, SQLite durability fixture, and Ed25519 observer fixture.
Passing the last two signifies only that the *synthetic defenses and
negative rollback controls* worked, not that rollback resistance or
independent custody has been established. The two-node experiment
is **not merged into that same-host receipt** because its signer
and worker intentionally use different hosts. Its output is research
evidence, not a trusted authorization receipt.

Lenovo's most recent 11-source result: preflight, resource,
durability-fixture, and observer-fixture PASS; other gates FAIL;
`decision=HOLD`, `agent_dispatch_authorized=false`.

For production readiness, the next **separate, explicit** work is:
1. Independent observer signing-key and pinned trust-root custody,
   published high-water and replay-safe state-change acknowledgements.
2. Source-authoritative linkage between each accepted broker transaction
   and an independently durable checkpoint; reject missing/late ACKs.
3. AppArmor profile approval, application and rollback tests on Lenovo
   without disabling global Ubuntu unprivileged-namespace protection.
4. Broker identity and credential separation, bounded real-provider
   network allowlists, spending quotas and revocation.
5. Requested model/subscription acceptance, protected disposable Git
   worktree authority, independent Codex review and exact-node proof.

**No live AI agent, network forwarding, production deployment, PR merge
or host security-policy change was performed in this slice.**


## Later research — two-stage synthetic observer ACK

A follow-on cross-node test now requires an Ed25519-signed prepare
record before committing a disposable SQLite receipt and a second,
state-linked signed checkpoint/ACK before reporting **synthetic**
acceptance. Physical x1-370→Lenovo results pass success, missing ACK
and tampered ACK scenarios. A lost ACK returns DENIED, but the SQLite
effect may already be committed; observer state is fenced until a
separately trusted reconciliation. These are not persistent or
independent signing roots or a crash-atomic distributed protocol.
See [OBSERVER_ACK_HANDOFF.md](OBSERVER_ACK_HANDOFF.md).
