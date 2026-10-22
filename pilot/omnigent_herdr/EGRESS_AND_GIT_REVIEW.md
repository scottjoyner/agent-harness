# Herdr/Omnigent: egress + disposable Git handoff

**Status: synthetic physical probes only — NO MODEL DISPATCH.**

## Why a Unix-domain socket

The agent's Linux namespace must not have an unrestricted IP stack or
access to its host's Tailscale, SSH keys, home directory, browser cookies,
or model-provider credentials. However, production Claude/Codex/OpenCode
normally require access to remote model services. A per-workload
socket-mediated broker is a possible *future* design. The current
`egress_broker_probe.py` is **DENY ONLY**: it never proxies TCP or HTTPS,
never opens an external socket, never handles a provider token, and never
accepts prompt text or arbitrary commands.

The physical Destroyer test exercises an AF_UNIX socket that is mounted
read-only as a single dedicated directory into a Bubblewrap namespace:
- Guest root: read-only `/usr`, private `/proc`, minimal `/dev`, one
  writable `/workspace`, and only `/run/fleet-broker/probe.sock`.
- Guest network: `--unshare-net` still blocks direct IPv4 egress.
- Broker: one ephemeral four-message protocol, with a synthetic challenge,
  `SO_PEERCRED` same-owner UID check, and every model/shell request denied.
- Positive test: only `schema=1,op=probe,challenge=<synthetic nonce>` is
  acknowledged as a synthetic probe, **never** as dispatch authority.
- Negative test: provider dispatch, arbitrary shell, wrong challenge, host
  secret visibility, and direct network connectivity do not succeed.
- Every test directory and socket is ephemeral. No daemon remains running.

### What this does NOT establish

`SO_PEERCRED` UID equality is **not a distinct-user identity**, especially
when all agent processes share one Linux account. A real broker needs
independently attested owner/session/worktree binding and a revocable,
short-lived capability; provider-specific method/path/host allowlists;
payload-size and token limits; replay/high-water protection; quota accounting;
timeout and concurrency limits; verifiable request/response provenance;
redaction of secrets and user data; and separate broker filesystem/network
and credential custody. Do not treat the synthetic nonce as authentication.
Explicit security review is required for any provider traffic and data-use
policies. The existing Fleet receipt *always* sets
`agent_egress_approved=false` and `agent_dispatch_authorized=false`.

## Why the Git fixture is a clone, not a linked worktree

`git_clone_probe.py` creates its own temporary seed repository and a
`--no-hardlinks --local` clone; it does **not** inspect or edit the user's
existing repositories. A linked `git worktree` has a `.git` pointer to
metadata outside the worktree; exposing that metadata for writes could
compromise other branches or refs. Until a separate secure worktree/metadata
design is proven, use a disposable independently owned clone.

The physical Destroyer test confirms:
- A synthetic `feature.txt` commit succeeds inside the private namespace.
- The clone is clean after the commit and only the expected file changed.
- The seed repository HEAD and working tree remain unchanged.
- An outside sentinel is untouched; direct IPv4 egress is denied.
- No push, remote SSH, PR creation, or user repository is involved.
- The temporary clone is deleted on exit.

The fixture does **not** prove production branch protection, safe hook
handling for arbitrary repositories, resistance to malicious repo content,
online CI provenance, commit-signing identity, cleanup after power failure,
resource/cgroup confinement, or independent CodeX review. Validate these
separately before exposing any non-fixture repository.

## Physical results — 2026-10-19

| Host | Strict namespace | Deny-only AF_UNIX bridge | Disposable Git commit | Agent admission |
| --- | --- | --- | --- | --- |
| Destroyer | PASS | PASS | PASS | **HOLD** — memory/swap pressure |
| Lenovo | FAIL | FAIL (namespace cannot start) | FAIL (namespace cannot start) | **HOLD** — AppArmor |

Lenovo's expanded `fleet_receipt.py` ingests seven already-generated
read-only JSON evidence files and produces only sanitized Boolean gates plus
their SHA-256 hashes. It reports preflight and resource PASS; AppArmor,
sandbox, catalog, synthetic egress, and Git FAIL. All missing, stale, and
forged gates must be treated as NO-GO. The receipts are not independently
signed or rollback-resistant.

## Next safe acceptance milestones

1. Obtain administrator approval for Lenovo's pinned Ubuntu vendor
   AppArmor profile, without disabling the global namespace restriction;
   require strict negative tests and rollback.
2. Requalify resources under a stable load; do not kill or disturb any
   resident models/services just to force the prefilter to pass.
3. Prove a distinct workload and owner identity for a *separately
   confined* broker, a deny-by-default provider allowlist, quotas,
   signed execution receipts, and no leaked provider credentials.
4. Prove safe real repository checkout/worktree authority, a clean
   rollback, and protected branch rules before first coding action.
5. Resolve model pin and real subscription differences (OpenCode's exact
   requested free DeepSeek pin is not listed), then run one approved,
   capped, disposable turn and independent Codex review.
6. Never infer production readiness from an offline unit suite, a
   synthetically healthy broker, or a CLI executable on PATH.

**No production installs, real agent launches, merges, or host policy
changes are authorized by these research probes.**


## One-namespace integrated synthetic canary (2026-10-19)

`offline_integrated_canary.py` extends the prior independent studies: a
**single network-unshared, minimal-root Bubblewrap session** commits a
fixture file in a disposable independent Git clone and exchanges four
JSON-RPC-like messages with a host-side deny-only Unix-domain socket.
It runs no LLM or real application source. Its host-side postcheck
requires a clean isolated clone, unchanged seed, unchanged private host
sentinel and secret, exactly one synthetic committed filename,
zero-capability and no-new-privileges witnesses, PID isolation and
a denied IPv4 connection. All temporary files and the socket are
discarded. Physical results:

- Destroyer **PASS** all integrated negative/positive checks. This is
  research evidence only; its ongoing services and memory/swap pressure
  still make multi-agent dispatch **NO-GO**.
- Lenovo **FAIL**, as AppArmor still blocks namespace creation; no
  host policy was modified, and its seed/guard remained unchanged.

The integrated result is now the **eighth** required Fleet receipt source.
The receipt requires exact Boolean fields, denies claims of forwarded
provider calls or real Git pushes, and checks that each evidence
document's observed host label matches the Lenovo preflight host label.
This prevents accidental combination of a Destroyer sandbox PASS with
Lenovo memory PASS. Hostnames are **self-reported, not signed
attestations**, so a compromised host can still forge labels; independent
witness custody is a separate release gate. Each JSON digest is computed
over the same bounded, no-symlink file descriptor used for parsing,
not a second filesystem read. All three physical synthetic probes
return nonzero exit status when their checks fail.

The network boundary is still **deny-only**. For a future connected
worker, do not grant general IP egress: a separate broker with
operator-verified identity, provider/domain routing, bounded quotas,
data-use approval, credential isolation, anti-replay and revocation,
audited timeouts and signed receipts would be required. The current
synthetic socket offers no such authority.
