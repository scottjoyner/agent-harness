# Synthetic source-owner claim inventory and Hermes revocation binding

Date: 2026-10-22. Host x1-370.
Research only / Agent Harness PR #18 DRAFT / decision HOLD.

## Why another check is necessary

The existing synthetic signed Hermes revocation checker at b90e602
required a caller-supplied expected claim list. It rejected incomplete
revocation lists RELATIVE to that supplied list, but did not authenticate
the origin or completeness of the list itself.

verify_hermes_source_owner_census.py now requires an additional
distinct Ed25519-signed SYNTHETIC source-owner claim inventory before
checking revoked claims against the existing synthetic fencing gate.
The source owner and revocation signer must have distinct public keys.
The inventory is strictly bounded (at most eight sorted unique SHA256
claim IDs), pins synthetic worker identity, fixed scope and signature
domain, digest, consecutive sequence, predecessor SHA256, retention
floor, and a short trusted-time validity interval.

The verifier takes exact caller-supplied owner public key/digest/floor
and revocation authority pins; it does NOT trust identity, claim count
or a revocation list delivered by an untrusted worker. It first
authenticates the entire source census, then derives the required
revoked claim set from that signed payload, and only then calls the
already tested synthetic revocation+quiescent Compose validator.
Extra expected_revoked_claim_sha256 or other unexpected keys in
fence arguments fail closed rather than overriding the owner inventory.

## Local test evidence

20 focused unit tests and four separate-process test cases passed.
They cover missing/partial source-owned inventory, partial revocation,
wrong worker ID, same key in both signing roles, signer replacement,
forged signature/fields, stale/expired/rewound inventory heads,
generation skips, replay with newer trusted head, unsorted/duplicate
claims, claim limits, incomplete quiescence, unknown fence overrides,
and source-owner-first validation before any fence evaluation.

An explicit opt-in, local two-process experiment keeps TWO temporary
Ed25519 private signing keys only in x1 parent-process memory.
A distinct child Python process receives only public keys and signed
synthetic receipts plus separately stated expected heads/floors.
It demonstrates:
- Two current signed owner claims matched by two signed revocations:
  synthetic-only PASS.
- Owner inventory lists two claims but revocation covers only one:
  DENIED.
- Old signed owner inventory against newer trusted head and floor:
  DENIED.
- Old signed owner and revocation states supplied WITH BOTH old
  trusted heads/floors: ACCEPTED, mandatory rollback counterexample.

No real source-owner API or real claim census has been consulted.
The signer identities are synthetic and both processes share the SAME
x1 host and Fleet Commander administration. A valid signature here
does not prove resource-side authorization, transaction completion,
revocation durability or independent custody. The claim list is
authentic relative to its disposable key only, not to real tasks.

## Mandatory release HOLD

Every response keeps source_owner_truly_independent=false,
external_head_independently_custodied=false,
resource_side_effects_attested=false,
production_no_dispatch_authorized=false,
production_rollback_authorized=false and decision=HOLD.

A real deployment requires an independently administered source owner
that can attest a COMPLETE task/claim inventory and resource-side
effects, an outside-fleet monotonic checkpoint/revocation authority,
physical partition/restart fencing and a reviewed immutable,
Watchdog-free knowledge source. No running Hermes mounts, production
claims, models, credentials, services, NAS files or provider endpoints
were modified or queried. Keep auto-assist PR #424 and P0 #414 on HOLD.
