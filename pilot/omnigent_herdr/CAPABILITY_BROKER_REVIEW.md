# Research-only scoped capability broker — Fleet Commander review

**Status (October 19, 2026): DENY-ONLY SYNTHETIC PROBE; PRODUCTION HOLD.**

This research slice adds `capability_gate.py` and
`capability_broker_probe.py`. No file in this slice contains real provider
credentials, external endpoint URLs, model inference calls, or an
authorized agent launcher. The broker is an ephemeral Unix-domain socket
fixture and deliberately forwards **zero** provider requests.

## What the current lease actually proves

The broker owns a fresh, local 256-bit HMAC key. A lease contains an
ephemeral token ID, research-only intent, exact owner/session labels,
a SHA-256-labelled worktree, exact requested model label, Unix UID,
short issuance/expiry interval (up to 60 seconds), epoch and at most
two synthetic receipts. The signed token is a canonical, base64url-encoded
JSON claim set with a domain-separated HMAC-SHA256 signature.

Broker validation, protected by a process-local lock, rejects:
- Invalid, altered or noncanonical signatures; wrong schema, operations
  or additional fields; malformed tokens and overlong frames.
- A different owner/session, model, worktree hash or Unix peer UID.
- Expired/future leases and backwards time within this broker process.
- Replayed or regressed sequence numbers, exhausted receipt count,
  revoked lease IDs, and older lease epochs.
- Any request to forward a provider call, run a shell command, or
  authorize a real agent.

Successful validation returns only
`{"status":"synthetic_receipt_only","dispatch_authorized":false,
"provider_requests_forwarded":0,"receipt_number":N}`.
There is no execution adapter behind this interface.

## Physical evidence

`capability_broker_probe.py` starts a **temporary** host-side broker
and executes a fixture client inside a minimal-root Bubblewrap sandbox.
The guest has one writable workspace and one explicitly mounted Unix
socket, no host credentials, disabled direct IP networking, an isolated
PID namespace, zero effective Linux capabilities, and NoNewPrivs.

| Host | Physical synthetic lease | Host credential read | Provider forwarding | Agent admission |
| --- | --- | --- | --- | --- |
| Destroyer | **PASS** | Denied | 0 | **HOLD** — memory and swap pressure |
| Lenovo | **FAIL** (namespace creation blocked) | Not established | 0 | **HOLD** — AppArmor policy |

On Destroyer, the guest tests two accepted synthetic receipts and eight
negative cases: replay, cross-owner same-UID scope mismatch, bad HMAC,
quota overrun, forbidden provider operation, revoked token, previous epoch,
and wrong model label. The host-side signing key remains inaccessible
to the guest and unchanged. The fixture cleans up its socket and workspace
after each run. Other provider workloads and local models are untouched.

## Explicit non-goals and remaining risks

This is **not production authentication**. The token is a bearer
capability. If another process on the same Unix account obtains it,
it may redeem the same owner/session's remaining synthetic receipts.
`SO_PEERCRED` same UID is **not independent human, workload or agent
identity**, and owner/session/worktree labels are locally declared
rather than externally attested.

The signing key, high-water counters, revocations and epoch exist only
in broker RAM. A broker restart loses state and cannot provide
independent, durable replay/revocation evidence. The wall clock check
is limited to the current process, and peer request framing/length tests
are a fixture, not a production-hardened gateway. Passing fixture
receipts is not proof that a real model accepted the pin or that the
provider's paid/free quotas were enforced. No independent signer or
rollback-resistant witness has been established.

Before any live model request, require a separately confined,
operator-approved broker with owner/workload attestation, independently
protected credentials and trust roots, pinned provider host/path/method
allowlists, strict token and payment ceilings, bounded concurrency,
payload/data-use policy, durable anti-replay and revocation, peer
separation, authenticated audit receipts and independent human review.
Testing direct provider networking from inside the namespace or
temporarily turning off AppArmor is **not an acceptable shortcut**.

The Fleet Commander `fleet_receipt.py` now requires nine per-host
machine-source records, including this lease test. It checks exact
Boolean witness fields and refuses any document claiming provider
forwarding or agent dispatch. It **always** outputs `decision=HOLD`
and `agent_dispatch_authorized=false`. The JSON hashes and machine
hostnames are not signatures or independent custody, and the receipt
itself cannot grant access.

**Operator action:** Review the pinned Ubuntu vendor Bubblewrap AppArmor
profile and rollback procedure in
[APPARMOR_LENOVO_REVIEW.md](APPARMOR_LENOVO_REVIEW.md), obtain explicit
administrative change authorization, and re-run all physical negative
tests on Lenovo before even considering further runtime testing.


## Optional SQLite durability prototype — later research slice

The original `capability_gate.py` remains ephemeral; a new, separate
`durable_lease_store.py` implements restart-safe SQLite transactions
for **synthetic receipt** state only. This adapter retains receipt
high-water, quota, epoch and revocations across cooperating process
restarts. Its old-database snapshot rollback vulnerability is
**reproduced and unresolved**, not waived by the test suite.
An Ed25519 checkpoint verifier can reject such rollback against a
**newer externally trusted checkpoint**, but permanent independent
signer custody, reliable publication and high-water authority are
not yet available. See
[DURABLE_BROKER_CHECKPOINT_REVIEW.md](DURABLE_BROKER_CHECKPOINT_REVIEW.md).
