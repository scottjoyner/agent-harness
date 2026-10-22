# Hermes signed claim-fence / quiescent fallback research

2026-10-22. Fleet Commander on x1-370.
Agent Harness PR #18 research only. Decision HOLD / no production changes.

## Motivation

A disposable sleep-only Compose fallback is safe from accidentally
executing the Hermes engine, but does not prove that existing leases
were revoked before the worker disappeared. A removed worker may have
already caused a side effect; a valid older task claim may be replayed.
The current running production Hermes still has its original unsafe
16-mount/9-writable configuration and restart unless-stopped.

## Additive contract

verify_hermes_fenced_quiescence.py composes:
1. A synthetic external Ed25519 authority receipt covering a strict
   explicit set of SHA-256 claim identifiers, synthetic worker
   fingerprint, bounded clock window, consecutive checkpoint sequence,
   predecessor digest, zero pending/in-flight/provider counters and
   explicit no-dispatch policy.
2. A separately caller-supplied *expected* authority public key/pin,
   exact signed-envelope SHA-256 digest, previous trusted head, floor,
   worker pin and complete expected claim set. The verifier does NOT
   derive trusted anchors from the untrusted signed receipt.
3. An exact synthetic quiescent worker-absence/no-restart/no-legacy-
   restore observation and strict validated sleep-only Compose layout.

All three must pass for synthetic_evidence_contract_pass=true.
Regardless, every outcome always reports:
external_head_independently_custodied=false,
source_owner_revocation_verified=false,
real_inflight_effects_fenced=false,
production_no_dispatch_authorized=false,
production_rollback_authorized=false and decision=HOLD.

No live task, lease, transaction, API, provider or production agent
is queried or modified. No signature is issued for real owner claims.

## Tested evidence

24/24 focused new research tests passed locally (20 verifier tests
plus four separate-process fixture tests). They reject forged/changed
signatures, substituted signers, missing revocation IDs, stale heads,
expired receipts, skipped checkpoint generations, worker identity
substitution, old legacy agent commands, unapproved network/mount
changes, pending claims/effects, and incomplete no-dispatch cleanup.

The explicit opt-in process probe creates a single ephemeral RAM-only
Ed25519 signer on x1, signs two sequential synthetic claim-revocation
receipts and sends only the public key/signed receipts and explicit
checkpoint expectations through JSON stdin to a DIFFERENT local
Python OS process. It produces:
- Fresh signed revocation at sequence 2 checked against retained head
  and sequence floor: PASS, synthetic evidence only.
- Old signed sequence 1 with newer expected head/floor: REJECTED.
- New signed sequence 2 missing one expected claim: REJECTED.
- Old signed sequence 1 supplied with OLD head AND OLD floor:
  ACCEPTED. This is a mandatory rollback counterexample, not a pass
  for production safety.
The fixture has real_task_claims_processed=0,
provider_requests_forwarded=0 and independent_authority_custody=false.

The process probe is OFF by default, prints HOLD and does no process
work until --synthetic-two-process is explicitly requested. It
does not store private keys, sign production claims, call Docker,
network, real Hermes or AssistX task APIs. The existing quiescent
Compose verifier is reused unmodified, not reimplemented.

## Critical unclosed gates

The parent signer and child verifier share x1 host administration.
All trusted keys, digest heads, sequence floors and clocks are
self-provided research inputs; an operator who can rewind them all
defeats the checker. The synthetic receipt's zero in-flight claims
and effects are self-reports; there is no resource-side signed commit,
remote claim census, independently administered revocation, or
partition-tolerant lease fencing.

The next REAL qualification needs owner-issued task/lease records and
a truly outside-fleet custodian retaining non-rollbackable revocation
heads, plus trustworthy source-resource effect receipts. No production
quiescence, deploy, merge, image promotion, NAS/Watchdog access,
credential mutation or rollback is authorized.

Keep auto-assist PR #424 DRAFT at its current authorized head,
auto-assist P0 #414 OPEN, Agent Harness PR #18 DRAFT, HOLD.
