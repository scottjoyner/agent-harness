# Durable synthetic V2 observer inbox — Fleet Commander handoff

**Research checkpoint: October 20, 2026. Draft PR / production HOLD.**

## Problem addressed

The previous x1-370 ↔ Lenovo V2 experiment verified three Ed25519
message types: a worker's signed outbox publication, the observer's
signed ACK and the worker's signed ACK-delivery receipt. However, the
observer kept only an in-memory sense of whether publication or ACK
delivery was uncertain. Restarting the observer could lose that fence.

This slice introduces a **separate single-operation, private SQLite V2
observer inbox**, `v2_durable_observer_inbox.py`. It binds the signed
outbox publication, signed observer ACK and signed worker delivery
receipt into a sequential, restart-readable local journal. It does
**not** couple the remote worker debit, observer ACK or delivery in
one distributed transaction.

### Persisted states and deliberate fencing

| Stage | Persisted artifact | Next action permitted |
| --- | --- | --- |
| `empty` | Signed observer genesis/initial synthetic state | Accept exactly one verified V2 publication |
| `publication_fenced` | Worker-signed publication + signed observer marker | Sign exactly one ACK, record it atomically |
| `ack_signed_fenced` | Signed publication + signed ACK + marker | Accept one matching signed worker delivery |
| `delivered` | Publication, ACK and signed worker delivery | No further operation, no model/agent dispatch |
| `quarantined_untrusted` | Signed observer marker of **unverified** worker claim SHA-256 | No ACK, retry, new publication or automatic release |

The quarantine stage matters when a worker claims a possibly committed
V2 debit but sends a forged or changed publication. Rejection alone is
**not** proof that no debit happened. An explicit fixture/operator
`quarantine_unverified` transition signs and durably records the
untrusted claim digest and exact operation ID without treating the
claim as authenticated. There is **no automatic unfence, retry,
refund, or replacement token**. Arbitrary hostile messages must not
be treated as a trusted source of worker state.

Each SQLite stage update stores the signed artifact and a matching
observer-signed marker in the **same local SQLite transaction**.
Reopening checks schema/application identity, private parent/DB owner
and permissions, no symlinks/hardlinks, pinned worker/observer public
keys, canonical signed artifacts, marker signature, matching
operation/head/state digests and exact stage cardinality. Missing
or changed events, a wrong key or an orphaned delivery fail closed.

The signer stays in the test supervisor's RAM and is passed to
cooperating short-lived processes. The private key is **not persisted**,
there is no unattended signer recovery, and no independent administrator
has approved its custody. A signer missing after a real machine
restart is a **HOLD**, not a reason to rotate automatically.

### Externally supplied head requirement

Every inspect or state mutation requires a caller-supplied exact
`(revision, SHA-256-of-signed-observer-marker)` head. Each transition
returns a new local head **after SQLite commits**. A caller presenting
a stale head is refused. If a signed ACK was committed but its response
was lost, a second call does **not** regenerate or reissue it; the
persistent `ack_signed_fenced` stage requires separate review.

This detects restoration of an old observer DB only when the caller
retains a newer, independently trustworthy head. The **mandatory
negative control** replaces both DB and supplied head with an older
valid pair; it is accepted. Separate immutable/monotonic anchor
custody is still absent, and hard rollback resistance is **NOT PROVEN**.

## Physical evidence

`test_v2_durable_observer_inbox.py` verifies signed state transitions,
stale-head refusal, wrong signing keys, forged publication/delivery,
no duplicate ACK, concurrent ACK contention, injected SQLite
marker-write rollback, tampered stored artifacts, missing DB,
symlink/hardlink and permissions denial, untrusted-claim quarantine,
and old-image rollback.

`v2_durable_observer_inbox_probe.py` runs independent, short-lived
observer **processes** for each transition, reopens the local
private SQLite file, and repeats those failure controls using a
disposable V2 worker debit/outbox. Its tests passed on:

| Host | Actual subprocess restart/fence | Forged delivery denied | Quarantine survives restart | Old-DB + old-anchor vulnerability |
| --- | --- | --- | --- | --- |
| x1-370 | PASS | PASS | PASS | Reproduced |
| Lenovo | PASS | PASS | PASS | Reproduced |
| Destroyer | PASS | PASS | PASS | Reproduced |

The cross-host `two_node_v2_outbox_probe.py --durable-inbox` mode
runs five synthetic x1-370 ↔ Lenovo message scenarios using that
inbox on **x1-370**. It reopens the inbox as new Python objects
between local stages; the separate same-host subprocess fixture is
what proves actual observer **process** restart. Cross-host scenarios
pass: valid publication/ACK/delivery, missing ACK, forged worker
publication, forged observer ACK, and forged delivery. Untrusted
publication now transitions to `quarantined_untrusted`.

None of these tests contacts an AI model/provider or launches an
agent. All fixture outputs assert
`agent_dispatch_authorized=false`,
`model_egress_authorized=false`, and
`provider_requests_forwarded=0`.

## Release admission

The existing Lenovo same-host Fleet Commander receipt now consumes
**15 evidence sources**, adding `v2_observer_inbox_fixture`. That
gate requires successful physical replay/fence controls, **including
explicit reproduction of the full valid-old-DB + old-anchor rollback
attack and durable unverified-publication quarantine**. It rejects
any claimed independent worker identity, external anchor, resource
source authority, rollback protection, persisted private keys or
agent admission.

Latest Lenovo 15-source result: **HOLD**. The inbox and other
previous synthetic fixtures pass, but resource prefilter fails at
approximately **7.72 GiB MemAvailable (<8 GiB)**. AppArmor,
strict sandbox, requested model catalog, network/egress, isolated Git,
integrated canary and sandboxed live-lease proof remain blocked.
The report remeasured **resource and V2 inbox** only; other evidence
files are retained from earlier checks. Do not call the unchanged
older gate inputs fresh physical verification.

Destroyer completed focused tests and synthetic restart probes with
scratch files removed. It is still resource-ineligible with very
little free RAM/swap.

### Outstanding trust and crash barriers

**This is cooperative restart durability, not production safety:**
- Worker public key is advertised over the same test connection;
  observer key and genesis/head anchor are also fixture-controlled.
  Distinct signing roles do not imply distinct administrators.
- The worker supplies the claimed outbox head and before/after
  state. Signed self-reports do not prove a real storage transaction.
- ACK and worker-delivery transit are not crash-atomic across nodes.
- No independently published monotonic observer checkpoint,
  immutable anchor, key rotation/revocation, or observer source
  attestation exists. Old DB plus old anchor defeats rollback defense.
- No production process relaunches a trusted signing key.
- This is a **single-operation fixture** and cannot drive runtime
  multi-agent dispatch or serve as a durable outbox publisher daemon.
- The V2 local debit+outbox intent atomicity from prior work remains
  limited to one synthetic worker SQLite transaction.

**Smallest next safe validation:** an independently administered
observer high-water publisher and resource-side trust root; durable
receipt identities pinned outside both worker and observer storage,
with source-attested debit IDs. Inject power/restart/partition faults
between V2 debit, publication, observer signed marker, ACK transit,
delivery and external high-water publication; deny automatic retries
or refunds until physical trusted custody is proven. Lenovo AppArmor
vendor-profile approval, headroom, exact free model pins/quotas and
independent review are separate admission gates.

**No real Herdr/Omnigent dispatch, provider traffic, production DB,
real coding repository writes, protected merge, security-policy
changes, public tunnel or existing-workload termination.**


## Follow-on: signed stage markers witnessed on another host

`v2_offhost_marker_witness.py` now validates the V2 observer inbox's
signed stage markers in a separate, bounded fsync'd JSONL file.
A Fleet Commander x1-370↔Destroyer test proved that a real restored
older observer SQLite image disagrees with a separately retained,
newer physical witness head. Stale/forked stages fail, as do restored
old witness logs paired with a *retained newer anchor*.

**No independent custody or immutable high-water:** the old witness
log still succeeds with its old anchor, and the same fleet
administrator controls both physical machines. This remains a
research experiment, outside the 15-source Lenovo admission receipt.
See [V2_OFFHOST_MARKER_WITNESS_HANDOFF.md](V2_OFFHOST_MARKER_WITNESS_HANDOFF.md).
