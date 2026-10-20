# V2 off-host witness-signed receipt — isolated physical research

**Fleet Commander checkpoint: October 20, 2026**
**Disposition: draft PR, NO-GO for agent dispatch, inference, production deploy, or merge**

## Gap addressed

The preceding V2 off-host marker witness on Destroyer verified x1-370's
Ed25519-signed observer states, maintained a private fsync'd JSONL record
chain and rejected stale heads. But it returned a **plain unsigned JSON
success response**. That proves what a cooperative SSH tool printed,
not that a distinct witness signing key attested the reported state.

This slice adds a separate ephemeral **Destroyer-generated Ed25519 key**
and a signed, exact-head witness receipt that x1 verifies independently
of the remote tool's success/return-code field.

### Contract implemented

`v2_witness_countersign.py` implements a deliberately limited,
one-operation, deny-only countersignature contract:

- A `bootstrap` on Destroyer generates a **fresh witness** Ed25519
  private key exclusively inside a single approved
  `$HOME/.cache/og-v2-countersign-XXXXXX/` scratch directory. The
  32-byte raw key file has mode 0600, no symlinks or hardlinks,
  same-user ownership, a 0700 parent, and fsync and directory fsync.
  Re-running bootstrap refuses to overwrite the existing key.
- A later **new one-shot SSH/Python process** reads only that private
  fixture key. It checks a V2 signed observer stage using the existing
  `V2OffhostMarkerWitness` validator, verifies the caller-supplied
  exact witness record head and observer marker head, and produces
  domain-separated `Ed25519` signature over:
  **witness signer fingerprint, observer and worker key fingerprints,
  observer and worker roles, exact witness record count/head,
  observer revision/marker head/stage, zero forwarded providers,
  no dispatch authorization.**
- `verify_witness_receipt` on x1-370 refuses altered signatures,
  wrong signing keys/pins, changed worker/observer roles, stale record
  heads, incorrect marker revisions and any attempt to set an agent
  dispatch flag. This is validation of an exact signed statement,
  not independent proof of how its signer was custodied.
- A lost reply after the witness log append can be re-read through
  `inspect` from another Python/SSH process. The signer reopens its
  disposable key and returns the **same deterministic Ed25519
  signature** over the already committed head. It does not append,
  retry the lease debit, refund the operation or release a fence.
- Three marker logs are allowlisted in the *disposable* directory:
  `primary`, `old_image` (rollback test), and `quarantine`.
  Only the primary log can bootstrap a key. Every signed log stage
  still follows the strictly bounded legacy V2 state machine.

No resident service, HTTP endpoint, agent process, provider adapter,
network model connection, protected repository checkout, non-fixture
private key, or production key provisioning is introduced.

### Physical x1-370 ↔ Destroyer verification

`v2_witness_countersign_probe.py` uses a disposable V2 debit/outbox
and private observer inbox on x1 and creates one ephemeral signing
root on Destroyer with an existing strict-known-host SSH connection.

Every `bootstrap`, `append`, and `inspect` request runs as a
**fresh remote Python process**; only its witness *public* key is
returned to x1. The source code and all three test logs and the
witness private key are removed by an EXIT trap after the bounded
run. No existing host process or security policy is changed.

The witnessed scenarios include:

| Physical scenario | Result |
| --- | --- |
| Three distinct in-RAM / disposable signing roles (worker, observer, witness) | PASS |
| Witness-signed genesis and worker-publication stages | PASS |
| Signed-ACK fence countersigned, retained across witness process restart | PASS |
| Lost ACK/readback: fresh remote process returns **identical signed receipt** for unchanged head | PASS |
| Stale signed receipt rejected when expected head advances | PASS |
| Changed witness key, modified receipt, missing head, stale writer rejected | PASS |
| **Actually restore an older valid witness log**, check with retained newer head | **Rejected** |
| Same older valid log paired with its **old** head | **Accepted — vulnerability reproduced** |
| Current witness log restored and final delivery countersigned | PASS |
| Unverified-publication quarantine terminal marker countersigned | PASS |
| Live provider traffic, model authorization, agent dispatch | Zero / denied |

Physical log rollback tests use only temporary research files in
the Destroyer scratch root and restore the newer disposable image
before completing the test. No production filesystem or Nextcloud
objects are touched.

**Automated tests:** dedicated unit cases for key permissions,
same-key cross-process reopening, receipt domain/key/role/head
binding and mutation, as well as deny-only physical report checks.
The pre-existing V2 observer/worker/fleet tests also run unchanged.

### What this does NOT prove

**No independent administrative custody.** Destroyer generated and
held its own *test* Ed25519 private key, but both systems remain
accessible to the SAME fleet operator. The witness public key is
introduced over the SAME trusted SSH bootstrap run. Its later
verification proves continuity with that bootstrap, NOT a trustworthy
out-of-band identity or external human approval.

The key is deliberately destroyed after the test. There is no
production key rotation, revocation registry, trusted hardware, or
trusted persistent signer, and no independent root that can prevent
a shared administrator from replacing both witness and observer
records.

**Critically, the witness can still countersign an old valid log
when supplied with its old anchor.** A malicious same-operator reset
of the log + trusted head is not detected. The witness is therefore
NOT immutable, monotonic or production rollback-resistant. No
resource-side authoritative transaction ID, signed storage receipt,
high-water publication outside shared fleet custody, or hard
partition/crash recovery authority has been verified.

The worker's V2 debit and its unsigned local HMAC outbox remain
one *local* SQLite transaction. Remote publication, signed ACK,
delivery and off-host countersignature are NOT distributed atomic
effects. A lost response MUST NOT trigger an automatic retry/refund.

### Deployment and approval gates

This is a **separate cross-host research fixture** and is deliberately
not added to Lenovo's **15-source same-host admission receipt**. That
receipt remains on HOLD because the approved AppArmor Bubblewrap
sandbox is not loaded, Lenovo does not currently meet the 8 GiB
resource prefilter, real requested model pins/quotas and provider
readiness are unverified, and owner-bound source authority is absent.
Destroyer itself lacks agent memory/swap headroom.

The next credible validation requires a genuinely **independent
administrative custodian** to provision and publish pinned witness
keys and signed policy, a separately protected monotonic head whose
rollback is outside the fleet operator's capabilities, and
resource-side signed transaction receipts. Prove crash/partition/
fork/revocation behavior before authorizing **one** isolated actual
agent task. Avoid changes to global AppArmor policy and never
automatically unfence uncertain transactions.

**Everything in this slice is synthetic: HOLD, NO-MERGE, NO-DEPLOY,
NO LLM DISPATCH, NO MODEL/PROVIDER FORWARDING.**
