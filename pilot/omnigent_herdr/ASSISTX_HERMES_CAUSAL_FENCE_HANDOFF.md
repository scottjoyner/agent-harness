# Hermes synthetic source-snapshot-to-revocation causal binding

Date: 2026-10-22. Host x1-370. Research only.
Agent Harness PR #18 DRAFT; auto-assist PR #424 DRAFT.
Decision HOLD; no production or real claim changes.

## Gap closed in the synthetic protocol

The previously signed owner claim census at c93aa98 and the signed
revocation receipt can describe IDENTICAL claim hashes, even when
revocation was issued BEFORE the source-owner snapshot. Merely
matching a list of IDs is not evidence that revocation addresses
the exact source snapshot to be quiesced.

verify_hermes_causal_claim_fence.py adds a separate signed Ed25519
synthetic causal binding from the revocation signer, with domain
separation from its revocation receipt. This link pins the exact
source-owner envelope SHA256, revocation envelope SHA256, both
sequences, worker identity, expected revocation signing key and
explicit consecutive binding generation/head/predecessor. It
requires verified owner inventory AND complete revoked claim set
AND qualified no-dispatch Compose / stop-removal contract BEFORE
it evaluates the new binding. Signer-reported times must order:
owner census issued, then revocation issued, then binding issued,
and all be unexpired at the externally supplied test time.

The binding is validated against separately supplied expected
digest/head and monotonic floor; self-pinning from the untrusted
record is forbidden. Unknown fields, signer substitution, old
revocation receipts, stale or skipped binding generations and
forged release/custody/effect flags fail closed. No real credentials,
source-owner requests, effect receipts, NAS/SSD or live containers
are read or modified.

## Verification

17/17 new causal-binding tests pass. Combined isolated staged
regression suite: 71/71 PASS, including the previously published
source-owner, signed fencing and Compose tests.

A new opt-in TWO-PROCESS probe uses distinct disposable Ed25519
source-owner/revoker private keys only in parent process RAM and
verifies in a child Python OS process using public keys and
synthetic receipts on stdin:
- Signed source census at t=100, revocation at t=120, separately
  signed binding at t=130, tested at t=140: SYNTHETIC PASS.
- Same exact claims but previously issued signed revocation at t=90,
  with a matching newly signed binding: REJECTED.
- Revocation receipt swapped AFTER signing the causal binding:
  REJECTED.
The child gets no private signing keys and no live API access.

## Non-negotiable limitations

All times are supplied by the disposable signers, NOT by an
independently trusted timestamp service. A malicious signing
administrator can forge ordering if they possess the signer key.
The trusted heads/floors, keys, clock and both signing roles
remain controlled on the SAME fleet host. Their joint rollback
remains a reproduced counterexample: signatures cannot make
same-admin storage rollback-proof. Zero pending/effects are still
synthetic assertions, not resource-side transaction evidence.

The pure verifier's success is ONLY synthetic_evidence_contract_pass;
every result has signer_timestamp_independent=false,
external_head_independently_custodied=false,
resource_effects_fenced=false,
production_no_dispatch_authorized=false,
production_rollback_authorized=false, decision HOLD.

Production remains running old Hermes image, 16 mounts/9 RW and
restart unless-stopped. No cutover, merge, restart, task dispatch,
real revocation, protected evidence access, or credential updates.

The next real milestone is an independently controlled source-owner
authority and a separate monotonic external checkpoint with trusted
resource-side effects. This synthetic work DOES NOT satisfy that
production gate. Keep AssistX P0 #414 OPEN.
