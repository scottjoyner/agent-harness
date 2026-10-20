# V2 detached custody checkpoint contract and three-node research

**Fleet Commander checkpoint — October 20, 2026**
**Decision: HOLD / PR DRAFT / NO MERGE / NO AGENT OR MODEL DISPATCH**

## Existing gap

An off-host Destroyer witness now signs receipt records for x1-370's
observer-signed V2 inbox marker, with exact witness and observer heads.
That signature is a useful way to detect changed/stale data **if its
public key and high-water checkpoint were pinned independently**.
However, the same fleet operator controlled the Destroyer key
bootstrap and the expected head. Even after adding a witness signing
key, an attacker could restore an old valid witness log and its old
claimed trust head.

The code had no distinct **external-custody verification policy** to
force callers to supply that third-party public key and expected
checkpoint digest, or to reject a stale witness receipt after a higher
record count was seen.

## New code: a deliberately deny-only, offline external-custody contract

- `v2_custody_checkpoint_contract.py` defines a detached, canonical
  domain-separated Ed25519-signed *synthetic V2 custody checkpoint*.
  It binds the **custodian**, **witness**, **observer**, and **worker**
  public-key fingerprints (four unique roles), the exact witness
  signed-receipt SHA-256, its record-count/head digest, the exact
  observer revision/signed-marker digest, a strict scope, checkpoint
  generation, prior checkpoint digest, issued/expires timestamps and
  mandatory `decision=HOLD` flags.
- Before a simulated custodian signs, the witness receipt must itself
  verify cryptographically under the caller-supplied witness public
  key and exact expected observer/worker identity and heads.
- `verify_research_checkpoint` MUST receive **separately supplied**
  trust inputs. It never selects a public key or head from the
  untrusted checkpoint: pinned custodian public key+fingerprint,
  witness key+fingerprint, observer key, worker pin, scope,
  expected generation, exact prior checkpoint hash, **exact expected
  checkpoint hash**, trusted clock and prior witness-count floor.
  Missing values, bogus signatures, role collisions, stale floors,
  mismatched identity/head, expired/future claims or generation reuse
  fail closed.
- `minimum_witness_count` is an **external caller-supplied floor**:
  the signed record count must be *greater*. Neither this floor nor
  the expected checkpoint SHA-256 is stored in the checkpoint itself.
  That prevents the verifier from treating the checkpoint's own
  self-reported head as independent corroboration.
- The envelope explicitly states
  `independent_custody_verified=false`,
  `source_transaction_attested=false`,
  `agent_dispatch_authorized=false`,
  `provider_requests_forwarded=0`, and `decision=HOLD`.
  A modified claim of administrative independence or model authority
  fails verification. No production grant/dispatch API is implemented.

**Crucial distinction:** requiring separately passed trust arguments
does not establish their physical or legal custody. The code cannot
verify that the supplied public pin, checkpoint digest, count floor or
trusted clock actually came from a different administrator. If one
fleet operator supplies all of those values, signatures merely prove
consistency within that operator's simulation.

## New code: disposable Lenovo custodian signer

`v2_custody_signer_fixture.py` provides bounded one-shot
`bootstrap` and `sign` commands. It creates a fresh 32-byte
Ed25519 private key in a private
`$HOME/.cache/og-v2-custody-XXXXXX/custodian-ephemeral.key`
(0700 root, 0600 file, exclusive creation, no-follow/hardlink checks,
fsync). No private key is transferred to x1-370 or Destroyer.
Later SSH/Python processes reopen this fixture key, verify a signed
Destroyer witness receipt and issue a deterministic, matching
Ed25519 custody checkpoint. Unknown request fields, duplicate JSON
fields, improper key files and non-disposable directories are denied.

The signer is a **candidate physical custodian test fixture**, NOT an
independent administrator, hardware root or production key service.
Its key and all other scratch evidence are removed at the end of
the experiment. The shared owner can still SSH to Lenovo and
directly access that test key while it exists.

## Fleet Commander three-node experiment

`v2_three_node_custody_probe.py` performs one synthetic V2 lease debit
and outbox+observer-inbox update on x1-370. It uses existing strict
known-host SSH to start *new* short-lived Python processes for each
request:

| Physical node | Role |
| --- | --- |
| x1-370 | synthetic worker + observer, verifier, operator-owned test pins/floors |
| Destroyer | separately keyed witness signer + private temporary JSONL marker chain |
| Lenovo | separately keyed simulated custodian signer + private temporary key |

Measured behavior:

| Scenario | Verified result |
| --- | --- |
| Four distinct worker/observer/witness/custodian public keys | PASS |
| Destroyer signed genesis and publication; x1 independently verified receipt signatures | PASS |
| Lenovo validated Destroyer's receipt and signed checkpoint generation 1 | PASS |
| Fresh Lenovo SSH/Python process reloaded its same disposable key and reproduced the **same signed checkpoint** | PASS |
| Missing externally supplied checkpoint digest, substituted custodian key, altered witness receipt | DENIED |
| Expired signed checkpoint (synthetic controlled clock) | DENIED |
| Observer signed-ACK fence → Destroyer signed new witness record → Lenovo signed generation 2 chained to checkpoint 1 | PASS |
| Old checkpoint checked against generation 2 and newer witness-count floor | DENIED |
| Old checkpoint checked against **its old floor, old digest and old generation** | **ACCEPTED — required rollback counterexample** |
| Provider calls / model egress / agent dispatch | NONE |

**Actual physical results:** Lenovo passed **67 focused tests**;
Destroyer passed **25 witness regression tests**; x1-370's
full pilot suite passed **521/521**. The one-shot three-node
physical fixture passed without errors. All temporary directories,
simulated private signing keys and test SQLite files are removed
by bounded cleanup traps when the physical command completes.
The clock values in this experiment are deliberately **synthetic**;
no production wall-clock/key-validity admission is being claimed.

The physical signer pins, witness heads and expected custody hash were
bootstrapped and carried by **the same x1 Fleet Commander operator
via ordinary SSH**. The experiment thus demonstrates protocol
interoperability and negative-control behavior only.

## What remains blocked

1. **Independent administrative custody:** the off-host Lenovo signer
   is not administered by an independent legal/operator identity.
   All trust roots were bootstrapped via the same operator. A shared
   administrator can reset the witness, its signing key, or the head.
2. **Monotonic external high-water:** no immutable witness generation,
   third-party notary, TPM-backed verified counter, independently
   custodied append-only log or external signed head publication has
   been deployed. The simulated floor is a function argument.
   Rolling back the signed checkpoint AND expected digest/count floor
   is still accepted.
3. **Source-authoritative transaction proof:** the worker remains
   capable of signing claims about its own synthetic SQLite state.
   No separately signed, resource-side transaction or side-effect
   receipt has been verified.
4. **Crash/partition safe fencing:** V2 worker debit + unsigned outbox
   is one local transaction only. Observer ACK, off-host witness and
   simulated custody checkpoint remain separate crash windows, with
   no automatic retry/refund/unfence permission.
5. **Runtime admission:** Lenovo's **15-source SAME-HOST** release
   receipt remains **HOLD**: missing approved sandbox/AppArmor,
   <8 GiB MemAvailable, real model catalog/quotas, authenticated
   provider and owner-bound execution evidence. Destroyer is
   resource-ineligible for agents. **The three-host research
   result is NOT added to the admission receipt.**

### Smallest next production-safe milestone

Ask an **actually independent administrator/custodian** to provision
and publish a pinned, separately controlled root and external
monotonic checkpoint head, using approved trust custody procedures
outside shared x1/Lenovo/Destroyer operator access. Require
out-of-band verification before this research contract can be
evaluated as a candidate gate. Then capture signed **resource-side**
transaction IDs and run crash/replay/revocation/split-brain tests,
including a restored database, witness log, and checkpoint floor.
Do not deploy Herdr/Omnigent or load models until those independent
requirements and device/sandbox gates are satisfied.

**No production deployment, PR merge, host-policy change,
provider request, LLM inference, live agent, production database
write, or protected user repository modification occurred.**
