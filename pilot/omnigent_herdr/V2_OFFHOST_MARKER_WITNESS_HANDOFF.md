# V2 observer marker witness on a separate physical fleet node

**Fleet Commander research handoff — October 20, 2026**
**Release: DRAFT / HOLD / NO-MERGE / NO MODEL OR AGENT DISPATCH**

## Why another witness is necessary

The opt-in V2 Herdr/Omnigent observer inbox on x1-370 now stores
a signed worker publication, observer acknowledgement and worker
delivery receipt in a private SQLite file. It also has a durable
signed `quarantined_untrusted` stage for possibly committed effects
reported through an invalid worker publication.

That observer database still has a rollback vulnerability: replacing
the SQLite image **and the trusted local head** with a previously
valid pair can make an old state look current.

This slice introduces a *narrow*, research-only **off-host journal**
for the observer's *signed stage markers*. The physical comparison
takes place between:

- **x1-370:** synthetic V2 worker debit/outbox, private observer SQLite
  inbox, ephemeral in-RAM worker and observer Ed25519 signers.
- **Destroyer:** one temporary, private, fsync'd JSONL witness log,
  verified by short-lived Python processes invoked through existing,
  strict-known-host SSH. No inference or agent workload is installed
  on Destroyer.

Separate hosts give a useful **physical observation** about rollback
scope. They do **not** establish independent administrative custody
because the same owner and existing SSH privileges can operate both.

## Implementation

`v2_durable_observer_inbox.py` now exposes
`export_signed_marker(trusted_anchor=(revision,sha256))`: a read-only
fetch of the already validated, observer-**signed** latest marker.
The private signing key is not exported, and the caller must supply
the exact current head. A read without that head is rejected.

`v2_offhost_marker_witness.py` verifies full Ed25519 signatures,
domain and strict marker shape under the *fixture-supplied* observer
public key. It enforces a single-operation allowlisted transition
graph:

```
empty (revision 0)
  ├── publication_fenced (1) → ack_signed_fenced (2) → delivered (3)
  └── quarantined_untrusted (4)
```

This graph rejects skipped stages, unauthorized second operations,
changes to worker/observer identity, changes to committed operation
and state across the publication/ACK/delivery stages, and any
attempt to leave terminal `delivered` or `quarantined_untrusted`.

Every record includes a contiguous record number, preceding record
SHA-256, and the entire observer-signed marker. Its line is canonical
JSON. The verifier rechecks **all** historic records and signatures
rather than trusting the last JSON line. It opens one bounded 0600
regular file under a 0700 owner-only directory without following
symlinks/hardlinks, locks the same file descriptor while reading or
appending, fsyncs the completed record, and validates its read-back.

No persisted worker, observer or model private key appears in the
witness log. No daemon, auto-repair, replay/retry/refund service,
model router, or production launch path exists.

**Required caller-held witness anchor:** every publish or inspect
needs the exact latest `(record_count, record_sha256)`. A stale
writer cannot append. `inspect` additionally requires the exact
`(observer_revision, signed_marker_sha256)` read back from the
observer inbox. A mismatch is a **HOLD**, never a release signal.

### Trust limitations

Although the witness verifies the observer's Ed25519 signature, the
pinned observer public key is supplied over the same *research* SSH
workflow. It is **not** administered or certified independently of
the x1 observer or shared fleet operator. The witness does not sign
a separate third-party acceptance receipt with its own durable,
independently administered private key; its success result is an
ordinary SSH response.

Most importantly, an attacker with shared admin custody can restore
a previously valid Destroyer witness log **and** the old trusted
anchor. The verifier will then accept the old log. A compromised
observer signing key can also sign false state claims. The witness
does not independently attest the worker's actual SQLite transaction
or any physical storage effect.

**Never equate this witness with independent reviewer approval,
external immutable monotonic high-water, rollback resistance,
resource-source attestation or live agent/model authority.**

## Actual Fleet Commander physical experiment

`v2_offhost_marker_witness_probe.py` prepares **disposable**
worker/observer SQLite databases and ephemeral Ed25519/HMAC keys on
x1-370, then uses a temporary private research folder on Destroyer.
Every stage invokes the remote witness as a **new short-lived
Python/SSH process**. The Destroyer scratch directory and Python
source files are removed after the test. No existing service is
restarted, no host settings are changed and no protected data is
copied.

| Test | Physical outcome |
| --- | --- |
| Signed genesis accepted on Destroyer | PASS |
| Signed publication and latest outbox head witnessed | PASS |
| Signed ACK remains fenced and remotely witnessed | PASS |
| Verified worker delivery / observer completion witnessed | PASS |
| **Restore actual older observer SQLite file**, compare to retained newer Destroyer head | **Mismatch denied** |
| Publish with stale Destroyer witness anchor | **Denied** |
| Restore older valid Destroyer witness log, compare with newer anchor | **Denied** |
| Restore old witness log **and old anchor** | **Attack reproduced — older log accepted** |
| Valid signed observer quarantine marker | PASS; no ACK authority |
| Provider forwarding / agent dispatch | Zero / denied |

The restoration test uses an **actual copied older SQLite image**,
not just a spoofed old hash. It then restores the newer disposable
image before finishing the normal signed delivery fixture.

**Automated tests:** 17 dedicated witness-log unit tests plus
five deny-only physical-result-contract tests, including missing
physical evidence and invented independent-custody/dispatch claims.
The complete pilot suite remains green. Run
`python3 -m unittest discover -s pilot/omnigent_herdr -p 'test_*.py'`
at the research branch checkout.

## Admission boundary and next safe milestone

This experiment is **intentionally NOT** folded into Lenovo's
existing 15-source **same-host** Fleet Commander qualification
receipt. Combining x1-370 success with Destroyer file checks would
hide Lenovo's still-failed sandbox, AppArmor, resource and model
admission gates.

The smallest credible follow-up is to give an **independently
administered custodian** control of the observer/witness trust roots,
a durable signing key and an external monotonic high-water mechanism
that x1 and the shared fleet operator cannot roll back. Verify
out-of-band pins, rotation/revocation and read-back proofs, then
stress crash/partition windows between source V2 transaction,
observer journal, witness publication, observer ACK and worker
delivery. Require exact source-attested transaction IDs, no
automatic retries or refunds, and an independent human review
before even a disposable real agent task can run.

**No real inference, model/provider tokens, live agents,
production databases, real user repository writes, merges,
production deployments, tunnel changes, security-policy edits,
or unrelated process terminations were performed.**


## Follow-on: Destroyer countersigns exact witness heads

A separate, ephemeral **Destroyer-generated Ed25519 key** now signs a
bounded no-dispatch receipt of the verified V2 observer marker and
exact physical witness-log head. Another SSH/Python process can re-open
the same disposable key after restart and reproduce identical signed
readback for the same committed head without a second write. Incorrect
keys, old heads, changed fields and forged model authority fail
cryptographic verification. The old valid log **with the old anchor**
still passes—therefore no independent immutable custody or rollback
resistance is established.

See [V2_COUNTERSIGNED_OFFHOST_RECEIPTS_HANDOFF.md](V2_COUNTERSIGNED_OFFHOST_RECEIPTS_HANDOFF.md).
