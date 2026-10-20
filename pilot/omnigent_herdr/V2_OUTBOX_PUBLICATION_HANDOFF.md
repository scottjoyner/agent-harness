# V2 synthetic outbox → observer ACK → worker delivery

**Fleet Commander physical research handoff — October 20, 2026.**

**Release verdict: HOLD / DRAFT / NO-MERGE / NO MODEL DISPATCH.**

This slice extends the opt-in V2 **single-SQLite atomic debit+outbox**
contract with an explicitly *separate*, cryptographically bound,
cross-host publication **fixture**. The outbox debit and its unsigned
HMAC intent remain atomic locally; the signed publication, observer
acknowledgement and worker delivery confirmation do NOT share that
transaction. The entire protocol is synthetic and has **no dispatch,
network-provider, payment, live repository, or production API code**.

## Code

- `atomic_synthetic_lease_outbox.py`: new read-only
  `export_head_event(trusted_anchor=(record_no,sha256))` verifies the
  complete local V2 outbox chain and exact latest head before returning
  one bounded head event. It does not export the HMAC key, HMAC value,
  bearer token, full request or unrelated SQLite bytes.
- `v2_outbox_publication.py`: domain-separated Ed25519 signed worker
  outbox publication, signed observer ACK, and signed worker ACK-delivery
  receipt. Every message binds the same operation ID, request/outbox
  SHA-256, exact V2 head, worker/observer identity and public-key
  fingerprints, and resulting synthetic lease-state digest. The ACK
  also includes the observer generation and prior checkpoint SHA-256.
  An observer will **not sign** an ACK until it has verified the
  worker's publication signature against the caller's expected
  head, operation, before-state and after-state.
  A worker will **not sign** delivery until it verifies the observer
  ACK signature and rechecks the current V2 outbox head.
- `v2_outbox_remote_worker_fixture.py`: restricted JSON-lines
  worker on Lenovo. It creates a private, temporary V2 SQLite DB and
  fresh in-RAM HMAC and Ed25519 keys; no real agent or provider calls.
  The worker's public key is advertised only through its disposable
  SSH fixture session.
- `two_node_v2_outbox_probe.py`: one bounded, strict-known-host
  SSH session from x1-370 to Lenovo. The observer signer remains in
  x1 process RAM. Only it can create the synthetic observer ACK;
  the worker signer remains in the Lenovo process. All exchanges
  explicitly deny agent dispatch, model egress and provider forwarding.

The experiment intentionally has **no** shared private signing key,
auto-retry/refund, automatic rollback recovery, resident agent
daemon, cloud tunnel, or production model router.

## Physical five-scenario matrix

| Scenario | Observed result | Observer decision |
| --- | --- | --- |
| Exact worker publication + observer ACK + worker delivery | All three signatures checked; signed delivery received | Fixture exchange complete **only**, runtime HOLD |
| Missing observer ACK after committed V2 intent | No signed worker delivery | Fixture marks uncertainty; HOLD |
| Modified worker publication payload | Rejected before observer ACK | HOLD |
| Modified observer ACK payload | Worker rejects ACK; no delivery | HOLD |
| Modified worker delivery receipt | Observer rejects; no accepted delivery | HOLD |

All five physical scenarios **PASS** on x1-370 ↔ Lenovo, with
`provider_requests_forwarded=0` and
`agent_dispatch_authorized=false`. The negative-scenario
`uncertain_operation_stays_fenced` flag is an **ephemeral decision
in the probe**, **not** restart-durable production fencing.
The remote worker is temporary and exits on EOF or error.

**Tests:** the full local suite is **361/361 passing** after this
slice. The focused new contract/worker/probe tests **37/37 passing**
on each of Lenovo and Destroyer (Destroyer receives and then removes
only temporary test Python files; no resident worker or services).
The outbox V2 local atomicity tests from the prior slice are still
present and passing.

## Mandatory trust limitations

The observer and worker public keys are introduced over the **same**
SSH fixture session. These are ephemeral *session pins*, not
independently administrated or externally verified trust roots.
The worker can sign a false statement about its own data; the
observer receives the previous and resulting state digests from
that same worker. The V2 outbox head passed to observer validation
is also supplied by the worker, not attested by independent storage.
Consequently signed messages here **prove consistency of claims**
between cooperative fixture processes, **not** that an independently
trusted resource performed the claimed real-world transaction.

Additional unresolved crash and persistence gaps:

- V2 debit+unsigned outbox intent are atomic **locally only**.
  Worker Ed25519 signing occurs later.
- Observer ACK is **not** atomically appended to an independent,
  durably protected checkpoint ledger in this research fixture.
- Neither observer nor worker signing key, high-water, delivery
  acknowledgement nor uncertainty fence survive test process exit.
- There is no durable deduplication/anti-replay authority across
  hosts, independently custodied fingerprint, revocation/rotation,
  hard rollback resistance, signed resource-side source attestation,
  or proof against partitions/split brain.
- A valid old SQLite image and an old purportedly trusted head can
  still be accepted. No production rollback fence has been established.
- The signer/transport proof does **not** override Lenovo's failed
  same-host sandbox and resource gates.

The existing **14-source Lenovo same-host qualification receipt is
unchanged by this cross-host experiment**, and remains HOLD. Do not
combine cross-host synthetic success with those separate failed
sandbox/identity/catalog checks to claim admission.

## Smallest safe next validation

1. Establish distinct, independently controlled/pinned signing keys
   and an externally published monotonic observer high-water/anchor.
2. Publish each V2 outbox event to a durable observer journal tied to
   the exact source-verified resource-side transaction ID; ensure
   signed ACK and delivery receipts are independently retained.
3. Inject process crashes at V2 commit, event export, signing,
   observer append, ACK transit and delivery. Require uncertainty to
   persist after restart and reject stale epochs/replay; no automatic
   debit retry, refund or forced unfence.
4. Obtain operator approval for the reviewed *narrow* Lenovo AppArmor
   profile, retain global Ubuntu restrictions, and requalify actual
   memory, sandbox, free-model catalog and quota before running one
   disposable real coding task with independent review.

**No real agents, provider requests, user repositories, production
deployments, host security-policy changes, existing-workload
terminations or PR merges occurred in this slice.**
