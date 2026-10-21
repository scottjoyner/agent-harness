# Hermes exact-image critical-source parity — physical checkpoint

**October 20, 2026; x1-370. Result: FOUR-FILE SHA256 MATCH; release HOLD.**

## Why this check exists

The previous Hermes disposable image canaries confirmed shell/Python task
behavior, but actual production mounts /app/src from a host worktree
read/write. The Docker image ID alone cannot establish whether the source
actually running in the container matches the image's baked-in source.
The local checkout revision also lacks independently verified build
and source authority.

## New read-only source parity guard

assistx_hermes_source_parity_guard.py:
- Dry-run by default; physical opt-in explicitly named.
- Restricts physical execution to x1-370, non-root host operator.
- Inspects ONLY Docker image ID and Mounts metadata of the current
  assistx-hermes-adapter (not Config.Env or mounted content).
- Verifies the active /app/src mount resolves to the exact expected
  source checkout, and records that it remains a **RW** bind.
- Requires a clean tracked local Git working tree and exact HEAD.
- SHA-256 hashes FOUR reviewed, nonsecret local source files:
  entrypoint.sh, strict_executor_main.py, strict_executor_adapter.py,
  agents/hermes_agent_adapter.py.
- Runs an isolated disposable sha256sum process against the exact
  running image object using pull=never, network=none, read-only rootfs,
  nonroot UID, cap-drop ALL, no-new-privileges, memory/PID/CPU caps,
  zero bind mounts and an overridden sha256sum entrypoint.
- Verifies exactly those four image hashes, no duplicates/extra paths.
- Rechecks running image ID, local HEAD and host hashes after snapshot.
- Any drift/missing output/unsafe input means HOLD and non-PASS.

## Observed physical result

Image object:
sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307

Host checkout:
14a5242159c06cbc157b60eed71ee28455ce689c
/home/scott/git/auto-assist-deploy-20261016

**Four image-baked SHA256 values exactly matched the host checkout:**
- entrypoint.sh: 49d4006f91e3ddb2e1bfd0f1db3c4a8bb6d08f071e39febf16d2dfc889d8dc70
- strict_executor_main.py: 17bfbc3190756ee1ecbdf4fc6eee374491606d167b5b9cb2cb5750cd97f74b75
- strict_executor_adapter.py: a8cc4e0b76ca9dd0e83a36290678535dc9cc33add170c440b00776713b66285c
- hermes_agent_adapter.py: eccfd7beea6170e12b2499883a46f8ce24ae9caf090c407613b9a9cc15f8048e

No network, provider, privileged mount, real task, or production change.
12 focused negative tests PASS; full pilot suite **809/809 PASS**.
The physical report records four_critical_file_hashes_match=true,
live_source_bind_verified_rw=true, but all release authority fields
false and decision=HOLD.

## Why this does not grant release

This is a bounded local equality check on four files—not a complete
image-to-Git-revision reproducibility proof, not remote GitHub source
custody, not a signed build provenance receipt, and not evidence of
filesystem immutability. The runtime /app/src mount remains RW, as do
two broad overlapping mounts to the protected watchdog evidence tree.
Existing production adapter uses 16 mounts, nine RW. No original service
deployment was restarted or changed.

The rootless entrypoint candidate, standalone Compose guard and
synthetic lifecycle from earlier commits remain research-only.
Full normal-entrypoint + mock-API physical integration, verified fresh
scoped index, protected actual data denial, source-owner authorized
execution, independent signer/revocation custody and rollback are
still separate release gates.

## Commands

Default HOLD/no Docker:

    python3 -B pilot/omnigent_herdr/assistx_hermes_source_parity_guard.py --json

One explicit physical read-only run on x1-370:

    python3 -B pilot/omnigent_herdr/assistx_hermes_source_parity_guard.py --physical-readonly-provenance --json

Neither produces a deployment grant. Keep agent-harness PR #18 DRAFT
and AssistX issue #414 OPEN until independently reviewed mitigation.
