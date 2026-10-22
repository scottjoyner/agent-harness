# V2 research external authority state — signed checkpoint and revocation

**Fleet Commander checkpoint: October 20, 2026**
**Status: research-only / draft PR / admission HOLD / no model or agent dispatch**

## Background and actual security boundary

The prior 3-node synthetic Herdr/Omnigent evidence flow consists of:

- **x1-370:** ephemeral Ed25519 worker and observer and a disposable
  SQLite worker debit/outbox and observer inbox.
- **Destroyer:** separate ephemeral Ed25519 witness signer with a
  private fsync'd, signed-marker log.
- **Lenovo:** separate ephemeral Ed25519 synthetic custodian signer
  with a private SQLite checkpoint journal. Sign operations enforce
  strictly increasing generations, full re-verification and durable
  **local** revocation; readback is read-only.

An attacker with the shared fleet operator's credentials can still
restore an old valid Lenovo SQLite image and undo that local
revocation. Even a signed checkpoint from Lenovo does not solve
revocation propagation to consumers that are using older valid
data.

## New implementation

`v2_external_authority_state.py` adds a **distinct, fifth signing role**
and deliberately narrow *offline* policy-state verifier. An
Ed25519-signed external authority state binds:

- Strict scope; five **distinct** signer fingerprints for authority,
  custodian, witness, observer and worker.
- Exact latest custody checkpoint SHA-256, custody generation and
  witness record count.
- Authority's own strictly scoped sequence number and exact signed
  predecessor state SHA-256.
- State `checkpoint_recorded` or `checkpoint_revoked`, a bounded
  validity interval, and required deny-only/non-independence flags.

The `verify_external_state` function verifies a signature over the
canonical signed payload, but only when the caller provides its
**own** pinned authority public key + fingerprint, expected exact
signed-state digest, expected sequence and predecessor, an externally
held minimum sequence, the role pins, scope and a trusted time. It
does not read those trust anchors from the untrusted state.

`check_checkpoint_evidence_only` requires BOTH the verified external
state and the already independently verified synthetic custodian
checkpoint/witness receipt. The authority state's checkpoint digest,
generation, and witness count must match exactly. A state marked
`checkpoint_revoked` **always makes this check false**.
`verify_revocation_evidence_only` checks that a matching pinned,
signed revocation state exists, for diagnostic/HOLD use only.

**These functions are evidence validators, not admission controls:**
a true evidence-validation result never confers rights to load a
model, forward provider traffic, run a shell, start an agent, retry
or refund a lease, unlock a sandbox or deploy Herdr/Omnigent.

## Real physical experiment

The existing `v2_three_node_custody_probe.py` now additionally
generates a **fifth, disposable, RAM-only research signing key on
x1-370** to simulate a separate external authority. The key does NOT
leave x1, but x1, Lenovo and Destroyer are **still under the SAME
administrative control**. This is a protocol simulation, **not** an
independent custodian or immutable checkpoint authority.

After Lenovo signs generation 2 of the synthetic custody checkpoint,
x1 signs an authority `checkpoint_recorded` state binding that exact
checkpoint digest/record count. The verifier accepts its correctly
pinned research evidence, but rejects an unsigned/mutated state,
a substituted authority key and a stale trust digest.

Lenovo then locally revokes its generation 2. x1 signs an authority
`checkpoint_revoked` state, with exact predecessor digest and
sequence 2. The physical fixture confirms:

| Physical/cryptographic check | Result |
| --- | --- |
| Fifth signing role is distinct from worker, observer, witness and custodian | PASS in test |
| Generation-2 custody checkpoint matched by signed research authority state | PASS |
| Authority key substitution | DENIED |
| Recorded old state with expected *new* revocation-state head/sequence floor | DENIED |
| Signed revocation authentic under exact retained head | PASS |
| **Actual older, unrevoked Lenovo SQLite image restored** | Lenovo local readback ACCEPTS (vulnerability) |
| Same restored Lenovo checkpoint under retained newer signed revocation | **External evidence check DENIED** |
| Same old valid recorded state supplied with its **old trusted digest/floor** | **ACCEPTED — mandatory rollback counterexample** |
| Revoked Lenovo image restored, new sign/readback | DENIED |
| Provider calls, agent dispatch, production writes | ZERO |

This proves *conditional* rollback detection if an observer really
retains a newer trust root and signed revocation outside an attacked
Lenovo SQLite image. It does **NOT** prove the root itself cannot be
reset by the same operator.

Verification evidence: **23 new focused unit tests** covering
signatures, role substitutions, revoked-checkpoint behavior,
generation/floor checks, time windows, missing authority pins,
retained-head refusal and the restored-old-authority-head
counterexample. The full x1-370 pilot suite: **584/584 passing**.
The 3-node physical experiment passed with the new synthetic
authority checks. The focused unit tests were also executed
successfully on Lenovo and Destroyer in disposable private
directories, with no production config or service changes.

The physical test never rebooted a host, and the x1 research
authority key is created anew on every run. The simulated
`issued_at`/expiry clock is fixed synthetic fixture time and not a
production trusted clock.

## Critical open gates

1. **Real administrative separation:** fifth-role key possession
   does not imply a different trusted operator. The authority signer
   was created by the same x1 Fleet Commander session that controls
   the other three nodes. Any trust roots, checkpoint digests or
   floors can still be rolled back by that operator.
2. **Externally persistent, immutable high-water:** no independently
   custodied key, signed notary publication, TPM-backed monotonic
   counter, protected append-only log, revocation registry or
   independently protected external head is present. Restoring the
   authority state **AND its expected head/floor** defeats this
   verifier even though its cryptographic signature remains valid.
3. **Source-authoritative actions:** the worker signs self-reports
   about a synthetic SQLite debit. No separately trusted
   resource-side transaction identifier or real side effect is
   independently attested.
4. **Distributed failure handling:** signing/SQLite atomicity is
   local only. Cross-node V2 debit, publication, witness append,
   observer ACK, Lenovo ledger and external authority state are
   not an exactly-once distributed transaction; crash/partition
   or rollback can leave uncertain effects.
5. **Runtime admission:** Lenovo's separate 15-source SAME-HOST
   release evidence remains **HOLD**. Available RAM is still near
   7.75 GiB against an 8 GiB floor. The approved narrow AppArmor
   profile has not been loaded, strict sandbox, real model pins,
   owner-bound admission and safe provider egress are unverified.
   Destroyer has insufficient RAM and essentially no free swap
   to host agents. The new remote research evidence is NOT
   merged into those same-host release gates.

## Next smallest safe step

Have an actual independent custodian **outside the shared fleet**
provision the signing root and publish a separately protected,
monotonic authority head and signed revocation index, including
key rotation/revocation and clock custody. Independently verify
these materials before allowing a remote signer or fleet endpoint
to consume them. Then record signed authoritative resource-side
transaction receipts and inject deliberate crash/partition/replay
faults with the real trust root. None of those steps has occurred.

**NO-MERGE / NO-DEPLOY / NO LIVE AGENT / NO MODEL OR PROVIDER
FORWARDING / NO AUTOMATIC RETRY OR UNFENCE.**
