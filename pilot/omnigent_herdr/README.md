# Herdr + Omnigent (`og`) guarded pilot

Target: **Lenovo**, not x1-370 production/research processes. Evaluated
upstream `jmvbambico/og` at `f3ed9ddd57d78e024724f4df741940f0e41e6585`
(2026-10-18). Upstream Herdr integration tests: **755 passed** locally on x1.

## Current status: PREPARATION ONLY

This folder does not install, start, connect, dispatch, expose, or authorize
an agent. The roster preserves the requested models but **no model pin or
subscription has been verified on a live CLI**. Claude's requested Opus 5.5
selection is intentionally not pinned in the `og` orchestrator YAML:
upstream reports that a native Claude model pin can strand non-Claude workers.

Run the offline check on a target machine with the intended shell PATH:

```bash
python3 pilot/omnigent_herdr/preflight.py --json
python3 -m unittest discover -s pilot/omnigent_herdr -p 'test_*.py'
```

The preflight checks local hostname and CLI names and restrictive roster settings;
it does **not** authenticate, make network calls, start daemons, or confirm
model availability. Its `dispatch_ready` result stays false until live,
separate acceptance evidence exists. Exit code 2 means prerequisites missing.

## Before executing an actual agent

1. Verify terminal permissions, sandbox enforcement, and isolated git
   worktrees, including a negative test that forbids writes outside worktree.
2. Audit both upstream `og` policies and native/ACP tool bypass routes.
   Do not rely on Omnigent policies as the sole filesystem or git guard.
3. On Lenovo only, install reviewed, pinned versions of `tmux`, `omnigent`,
   `herdr`, and each CLI/ACP bridge using user-controlled paths. Do not run
   remote installer pipes, unreviewed auto-updates or public tunnels.
4. Run `og` installer `--check`, `--questions`, and `--plan ... --dry-run`
   using actual CLI-discovered models and a local-only port.
5. Verify actual orchestrator model, each free-tier/quota classification,
   vendor identities, CLI acceptance, and one real model turn per worker.
6. Execute a disposable repository task (maximum two concurrent coders);
   require a substantive independent Codex review plus isolated CI checks.
7. Preserve machine/session/model/commit and test receipts. Stop with an
   unmerged PR. Never enable automatic merging or production deployment.

## Fleet integration boundary

Herdr owns terminals; Omnigent owns agent sessions and routing; Fleet
Commander owns physical readiness/SSH placement. AssistX/Auto-Router owns
admission if integration is later approved. Only sanitized execution
receipts should cross into fleet dashboards; omit prompts, keys, OAuth
tokens, user files, and full source diffs.

No process-killing, node deployment, GitHub publication, or merge is
authorized by this pilot.

## Registry correction (2026-10-18)

Upstream `og` identifies Command Code's actual executable as `cmd-acp`.
The pilot requires this ACP entrypoint; `cmd` is only used by the optional
upstream model-catalog listing and is not a second mandatory runtime executable.
The preflight has been aligned with that registry contract. Model discovery,
authentication, and a real ACP dispatch remain outstanding.


## Physical sandbox qualification

Run `python3 sandbox_probe.py` on each proposed Linux agent host.
The probe builds a disposable bubblewrap namespace, verifies a writable
workspace, attempts a denied write to a readable out-of-scope sentinel,
and attempts a network connection that must be unreachable. It never
launches an AI model or agent. Exit 0 means sandbox test passed, **not**
that the host is admitted to run agents.

Physical Fleet Commander evidence on 2026-10-18:

- Lenovo (Ubuntu 24.04): **DENIED**; bubblewrap cannot create the network
  namespace (or user mapping). No agent dispatch.
- Destroyer (Ubuntu 24.04): **SANDBOX PASS** (filesystem + network
  denials); **ADMISSION HOLD** because the node has only about 3.2 GiB
  memory available and nearly full swap while already serving workloads.
- Xwing and x1-370: unprivileged user namespaces are blocked in
  read-only preflight; full sandbox admission unverified.

Do not disable AppArmor globally or run workers outside the sandbox.
Re-qualify after any host-security changes and verify credentials, model
pins, quotas, branch protections, resource isolation, and human review
independently before any real dispatch.

## Fleet Commander catalog and capacity checks (2026-10-19)

The standalone checks below are read-only and **never authorize dispatch**.
They do not contact an LLM inference endpoint or use operator credentials.

```bash
python3 pilot/omnigent_herdr/catalog_probe.py --json
python3 pilot/omnigent_herdr/resource_probe.py
```

`catalog_probe.py` runs upstream model-listing CLI commands with a
**disposable HOME/XDG directory and an allowlisted environment**. Provider
API keys and credentials are not inherited. Success means *only* that the
requested identifier was in the first column of a successful catalog
response; it is not proof of auth, quota, free pricing, pin application, or
a successful coding turn. Failure/timeout/missing CLI blocks catalog
qualification. This avoids silent paid-model fallback.

Lenovo physical observations (user-scoped pilot directory):

- `kilo` 7.8.12: `kilo/kilo-auto/free` **listed**.
- `cmd` 1.84.0 with `cmd-acp` 0.2.0:
  `deepseek/deepseek-v4.1-flash` **listed**. ACP consumes JSON from
  standard input; do not invoke it with untrusted shell-script stdin.
- `opencode` 1.18.35: requested
  `opencode/deepseek-v4.1-flash-free` **not listed**.
- Freebuff wrapper 0.2.30: official release archive SHA-256
  `f3f3776bee033ccda17dab54c6d42cb24438b24b4f3139abddd6e5ecc45d6029`;
  native Linux binary and Tree-sitter file were extracted into a user-only
  prefix, with **no TUI startup, login, or agent/model session**. We did
  not invoke the wrapper (it can auto-update); the binary must not be
  launched before verified sandbox admission. BuffLINK `blink` v0.4.0
  is now built and verified separately below.
- Claude 2.1.303 and Codex 0.163.0 executables report versions;
  their subscriptions and requested Opus 5.5 / `gpt-6-luna` pins
  remain unverified.

`resource_probe.py` is a **prefilter**, not an admission authority.
For a two-coder pilot it requires 8 GiB MemAvailable, 15 GiB free disk,
1 GiB free swap when swap exists, and a one-minute load per CPU <= 0.8.
The thresholds are conservative initial engineering values, not measured
multi-agent capacity requirements. Lenovo passed the resource prefilter
but **failed the physical sandbox**. Destroyer passed the sandbox but
**failed the resource prefilter** (about 3.45 GiB available RAM and
essentially no free swap); do not disturb its workloads.

**Final decision: HOLD.** Do not run `og start`, `blink`,
`freebuff`, or any actual agent dispatch on Lenovo or Destroyer until
both physical isolation and capacity pass and all credentials/pins/quotas
are separately verified.

## October 19, 2026: vendor AppArmor and BuffLINK research

See [APPARMOR_LENOVO_REVIEW.md](APPARMOR_LENOVO_REVIEW.md) for the pinned
Ubuntu-vendor `bwrap-userns-restrict` policy, its offline parse evidence,
and the approval-only/rollback procedure. **The profile has not been loaded
into the kernel.** Do not relax Ubuntu's system-wide namespace restriction.

### Bridge version remediation and runtime boundary

Initial BuffLINK `v0.1.2` at commit
`ebe91c2374195ddd83e022f275e3b246285683e5` passed **100/100
offline Rust tests**, but its ACP-only binary did **not** implement
`BLINK_MODEL` or `--list-models`. This makes it unsafe for the pinned
Freebuff assignment even though Omnigent generates a model field.

BuffLINK **v0.4.0** at commit
`8f1154048f47e2b5069ebf98b2b1ec3b6f494b3d` was compiled
with Rust/Cargo `1.90.0`, passed **251/251 offline Rust tests**,
and installed in the isolated Lenovo prefix.
Binary SHA-256: `8a6eb287ed656ae31795bdc7699d36c40044eae23dc5090ce220bf6e00d34b32`.
Its `--version` reports 0.4.0 and an ACP-only `initialize` request
returns protocol version 1. No `session/new` or inference was issued.
Source inspection confirms `BLINK_MODEL` matching and rejection logic
exists; it has **not** been verified against a live Freebuff picker.

**Do not run `blink --list-models` as a harmless inventory command.**
On v0.4.0 it spawns the native Freebuff TUI in a PTY and navigates
the model picker. The catalog probe intentionally marks Freebuff
`requires_sandbox` instead of invoking that command on an unqualified
host. A live model-catalog check needs a physical sandbox and a reviewed
non-sensitive test session.

The native Freebuff v0.2.30 executable is staged, SHA-256
`05673bb64a561d50cd2b770dc7bd13dd7925388d8b817b2da856dfdb500465f0`,
but has **not** been executed or authenticated.
`preflight.py` requires both `blink` and `freebuff`, plus Node.js
for the Node-based CLIs. Physical Lenovo now reports
`configuration_valid=true`, `bootstrap_ready=true`, and
**`dispatch_ready=false`**. The native binary, CLI catalogs, or
structural readiness alone cannot authorize an actual agent session.

### Isolated `og` configuration-only installation

Using `og` source `f3ed9ddd57d78e024724f4df741940f0e41e6585`,
a config-only install was performed with a separate disposable HOME and
`OMNIGENT_HOME` under Lenovo's pilot work directory. No `og start`,
public tunnel, or background server was invoked. Its plan had
`auto_update=false`, `ngrok_domain=""`, and
`max_dispatches=2`. Omnigent `0.18.0` loaded the generated bundle and
resolved exactly the five requested coder/reviewer `executor.model`
pins. **That proves structural YAML model-pin placement only**, not
vendor catalog acceptance, authentication, quota, model availability,
tool permission boundaries, or an independent Codex review.

## Final October 19 physical gate (read-only)

After installing the verified Freebuff native executable and upgrading
BuffLINK to v0.4.0, Lenovo's `preflight.py` reports
`configuration_valid=true`, `bootstrap_ready=true`,
`dispatch_ready=false`, and no missing CLI names.

The updated `catalog_probe.py` checks only Kilo, OpenCode and Command
Code. Kilo's requested `kilo/kilo-auto/free` and Command Code's
`deepseek/deepseek-v4.1-flash` are listed, but OpenCode's requested
`opencode/deepseek-v4.1-flash-free` is **not listed**. Freebuff's
requested `deepseek/deepseek-v4-flash` remains `requires_sandbox`:
BuffLINK v0.4.0's catalog command drives the Freebuff TUI and cannot
be invoked under the current Lenovo AppArmor restriction.

The vendor AppArmor candidate has passed digest + offline-parser
verification, **but it is not loaded**. The physical `sandbox_probe.py`
still fails with `RTM_NEWADDR: Operation not permitted`.

A new resource check reports **7.924 GiB MemAvailable**, below the
8 GiB pilot threshold; the other resource subchecks pass. Two resident
`llama-server` processes were observed (approximately 3.0 GB and
1.8 GB RSS). They were not stopped or reconfigured. The prefilter
therefore reports **NO-GO** even if the AppArmor exception is later
approved. No services, credentials, background servers or model
sessions were changed.


## Strict isolation and Fleet Commander receipt (2026-10-19)

The earlier `sandbox_probe.py` mounts the host root read-only. This
blocks writes but **does not deny reading host secrets**, so a pass must
never admit a model worker. The new `strict_sandbox_probe.py` tests a
minimal-root namespace: writable isolated workspace; host secrets
unreadable; out-of-workspace writes rejected; outbound network blocked;
host PIDs isolated; Linux capabilities cleared and no new privileges.
Destroyer passes this stricter physical probe but fails memory/swap
qualification. Lenovo remains blocked on the vendor-profile activation.

Run the following in the research pilot (no inference and no deployment):

```bash
python3 pilot/omnigent_herdr/strict_sandbox_probe.py
python3 -m unittest discover -s pilot/omnigent_herdr -p 'test_*.py'
```

`fleet_receipt.py` accepts five already-generated JSON evidence files,
checks their schema and typed gate values, and produces a sanitized,
hash-referenced Fleet Commander decision. It always sets
`agent_dispatch_authorized=false`. The latest Lenovo physical receipt
reports preflight PASS, resource prefilter PASS, strict sandbox FAIL,
catalog FAIL, AppArmor activation FAIL, and **HOLD**.
Source files and credentials are not included in its output.
The receipt is not signed or an authoritative replay-resistant witness.

The requested model IDs are also locked down in `preflight.py`.
Changing a free model to a paid Kilo tier, substituting the Codex
reviewer model, changing the Claude requested model, or changing the
evaluated `og` commit now makes `configuration_valid=false`
until the change is reviewed explicitly.

Passing strict isolation would still leave hosted-model connectivity,
real agent launch wrapping, authentication, quota/price verification,
and independent Codex review unproven. Do not bypass those gates.


## Synthetic model-egress and disposable Git checks (2026-10-19)

See [EGRESS_AND_GIT_REVIEW.md](EGRESS_AND_GIT_REVIEW.md) for threat model and
the handoff for future owner-bound model access. `egress_broker_probe.py`
physically verifies that a network-disconnected Linux sandbox can reach an
ephemeral **deny-only** host-side Unix socket. No model endpoint, secret,
real request payload, or production process is involved. The accepted
probe command is synthetic and never authorizes dispatch.

`git_clone_probe.py` exercises an offline Git commit inside an isolated
disposable clone, proves the original fixture remains unchanged, and
removes its temporary files on exit. It intentionally does not mount a
linked worktree's host `.git` administrative metadata or use a real
repository. Both probes pass on Destroyer and fail on Lenovo before
the sandbox can start. Destroyer remains resource-ineligible.

`fleet_receipt.py` now includes **seven** fail-closed evidence checks:
preflight, resource, strict sandbox, catalog, AppArmor, deny-only
socket transport, and isolated Git. It does not accept a broker that
has forwarded provider requests or a Git fixture with an attempted
remote push. It returns HOLD regardless of synthetic check results.
Its SHA-256 evidence references are not equivalent to independent
signature/custody or immutable logging.

On the latest Lenovo physical pass the only green checks were preflight
and resource. No model turns, merge, auto-update, public tunnels or
production process changes were performed.


## Fleet Commander same-session canary and host-bound evidence (2026-10-19)

`offline_integrated_canary.py` performs a **single-sandbox**, offline
fixture run combining Git commit + deny-only Unix socket transport.
On Destroyer it passed, while Lenovo was blocked by its unchanged
AppArmor policy. No real agent was invoked, no external API accessed,
and no real repository was used. Destroyer remains resource-ineligible.

`fleet_receipt.py` now requires eight JSON inputs:
`preflight`, `resource`, `sandbox`, `catalog`, `apparmor`,
`egress`, `git`, and `integrated`. All probe outputs carry an
`observed_host` string, and the receipt rejects mismatched host labels
to prevent mixing Destroyer's sandbox test with Lenovo's resource test.
Hostnames themselves are **not cryptographic identity evidence**. The
receipt reads each file once with `O_NOFOLLOW`, size bounds and
descriptor-stability checks, and hashes those exact bytes. The result
always keeps `agent_dispatch_authorized=false`.

The synthetic socket, Git and integrated probes now exit with status 2
if the physical checks fail, making CI/shell gates fail-closed. The
Lenovo decision is still **HOLD** until the AppArmor, complete model
catalog, controlled egress, independent review, and runtime custody
gates are actually satisfied.


## Scoped synthetic lease checks (October 19, 2026)

See [CAPABILITY_BROKER_REVIEW.md](CAPABILITY_BROKER_REVIEW.md).
`capability_gate.py` provides **research-only**, ephemeral,
domain-separated HMAC-SHA256 lease validation for synthetic receipts.
It checks owner/session/worktree/model labels, Unix UID, short expiry,
monotonic sequence, count limits, revocation, epoch changes and
signature tampering. Nothing in this broker can forward an API call,
execute a shell command or authorize dispatch. Real provider quotas,
identity attestations and cross-restart replay protection are absent.

`capability_broker_probe.py` tests that gate over a real AF_UNIX socket
from inside a network-disconnected Bubblewrap sandbox. Destroyer passes
the physical negative and positive checks, but remains resource-ineligible.
Lenovo fails before sandbox setup owing to unchanged AppArmor policy.

The Fleet Commander decision now consumes **nine** host-labeled JSON
inputs: preflight, resource, strict sandbox, catalog, AppArmor,
deny-only socket, isolated Git, same-session integrated canary, and
synthetic lease. A synthetic success is **never** a signed grant and the
receipt always returns HOLD. On Lenovo only preflight and the resource
prefilter passed at last sampling; no agent is authorized.


## Restart durability and independent observer research — Oct 19, 2026

See [DURABLE_BROKER_CHECKPOINT_REVIEW.md](DURABLE_BROKER_CHECKPOINT_REVIEW.md).

`durable_lease_store.py` persists **synthetic** lease IDs, quotas,
high-water counters, revocations and epoch in owner-private SQLite.
`durable_restart_probe.py` demonstrates process restart and multiple
concurrent cooperators on Lenovo and Destroyer. Both PASS. Crucially,
both also prove restoring an old valid DB can resurrect a receipt:
`file_rollback_protection_verified=false`.

`observer_checkpoint.py` verifies domain-separated Ed25519 checkpoints
without the worker signing key and checks against a supplied trusted
generation floor and pinned observer public key.
`observer_replay_probe.py` passes the signed-state and old-snapshot
negative controls on both machines, but is **only an ephemeral signer
fixture**; no independent custody or continuous publication is proven.

`two_node_checkpoint_probe.py` runs a disposable cross-node test with
the observer Ed25519 private key only in `x1-370` RAM and a distinct
HMAC worker secret only on Lenovo. This physical experiment PASSed:
the x1 observer rejected Lenovo's rolled-back synthetic SQLite image
against the newer signed checkpoint. It is not trusted, permanently
published anti-rollback state; an old checkpoint plus an old generation
floor still passes, and the observer cannot attest to the remote
fixture's execution source.

`fleet_receipt.py` now requires **11** host-specific evidence sources
and continues to return HOLD. The two-node cross-host evidence is
kept separately, not falsely combined with Lenovo's same-host gates.

These results do not authorize an AI agent, real model egress, host
policy edits, deployment, or a PR merge.


## Signed prepare/commit-ACK synthetic handshake (2026-10-19)

See [OBSERVER_ACK_HANDOFF.md](OBSERVER_ACK_HANDOFF.md). The research-only
`observer_ack_protocol.py`, `ack_remote_worker_fixture.py` and
`two_node_ack_probe.py` test a **two-stage signed preparation and
post-commit observer ACK** between the x1-370 observer and Lenovo
synthetic SQLite worker. The worker verifies both an Ed25519 checkpoint
for the actual synthetic DB state and the operation-linked signature
on the ACK before returning any synthetic success.

Physical tests PASS for valid ACK, missing ACK, and tampered ACK.
Missing or invalid ACK returns DENIED and leaves the observer fenced,
even if the local SQLite debit already committed. This is not
distributed crash-atomicity or source-attested observer custody;
the signing key and high-water are still ephemeral fixtures, and
the observer public key was introduced over the same SSH test channel.
The worker and observer are **not authorized to perform any real
model request or agent execution**. These cross-host witnesses are
not inserted into the 11-source same-host Lenovo admission receipt.


## Restart-durable synthetic observer fence (2026-10-19)

See [DURABLE_OBSERVER_JOURNAL_REVIEW.md](DURABLE_OBSERVER_JOURNAL_REVIEW.md).
`durable_observer_journal.py` stores signed prepare, pending ACK,
monotonic local checkpoint and cooperative confirmation state in
an owner-private SQLite file. Every reload re-verifies signatures and
journal/checkpoint consistency; uncertain prepare or ACK delivery remains
fenced across a cooperating observer-process restart.

`durable_observer_journal_probe.py` passed on x1-370, Lenovo and
Destroyer, including tampered ACK rejection, post-restart fence checks,
and the mandatory old-valid-DB rollback vulnerability reproduction.
This is **not** an independently controlled observer or a
source-authoritative worker delivery receipt. Cooperative fixture
confirmation must never be interpreted as production approval.

`fleet_receipt.py` now requires **12 same-host evidence inputs**.
The latest Lenovo 12-source result stays HOLD: only preflight and the
three synthetic durability/observer fixtures passed. Resource memory
headroom was ~7.78 GiB, below the 8-GiB floor, and strict AppArmor
namespace activation remains unapproved. No AI agent has been launched.


## Separate signed cooperative witness log (2026-10-19)

See [CHECKPOINT_WITNESS_REVIEW.md](CHECKPOINT_WITNESS_REVIEW.md).
`checkpoint_witness_log.py` supplies a bounded, private, hash-linked
Ed25519 checkpoint log that requires a caller-provided trusted
generation/head hash. A reader is read-only; the publisher refuses
stale writer anchors and interrupted/altered records. An external
anchor is **not automatically trusted**—this pilot uses an ephemeral
in-memory value and does not have independent storage/custody.

The `two_node_witness_probe.py` cross-node synthetic run succeeded:
x1-370 held the observer signer and witness log, Lenovo supplied
disposable synthetic SQLite snapshots. An old worker DB and an old
log were rejected when the newer witness head was retained. An old log
paired with an old anchor was still accepted (mandatory negative
control). The witness remains research-only and is intentionally
**not** added to Lenovo's same-host 12-source admission receipt.


## Worker-signed synthetic commit and ACK-delivery receipts (2026-10-19)

See [WORKER_SIGNED_ACCEPTANCE_REVIEW.md](WORKER_SIGNED_ACCEPTANCE_REVIEW.md).
`worker_acceptance_receipt.py` defines exact, domain-separated Ed25519
commit and delivery receipt shapes. `ack_remote_worker_fixture.py`
generates a distinct ephemeral Lenovo signer and signs the actual
synthetic SQLite post-commit digest and, after validating an observer
checkpoint/ACK, a second receipt bound to both observer signatures.
The `two_node_ack_probe.py` observer verifies these worker signatures
**before** checkpoint publication or ACK-delivery confirmation.
Physical x1-370↔Lenovo success and four negative scenarios passed.

Lenovo's worker signing public key is bootstrapped through the same
research SSH channel; **independent resource identity, source
attestation, durable worker receipt storage, continuous publication,
and permanent key custody remain NOT verified**. Every result remains
deny-only; the separate cross-host synthetic proof is not added to
Lenovo's same-host admission receipt, and real agent/model dispatch
remains HOLD.


## Restart-durable synthetic worker-signed receipts (2026-10-19)

See [DURABLE_WORKER_RECEIPT_REVIEW.md](DURABLE_WORKER_RECEIPT_REVIEW.md).
`durable_worker_receipts.py` stores exact Ed25519-signed synthetic
worker-commit and ACK-delivery records in a separate private SQLite
journal. All cooperative reads and writes require a caller-supplied
exact trusted head. A pending commit blocks another operation until
a matching signed delivery is recorded; missing/wrong-key/corrupted
DBs and stale or forked heads are rejected.

`durable_worker_receipts_probe.py` runs short-lived worker fixture
processes with fresh in-RAM signing keys and separate throwaway
lease/receipt SQLite files. All three physical fleet nodes passed
the synthetic restart and rollback tests. **Crucially**, the probe
also reproduces a lease debit committed WITHOUT a signed receipt,
and a valid older receipt DB restored alongside an old head.
Neither atomicity nor external high-water custody is established.

Lenovo's Fleet Commander receipt now requires **13** same-host
evidence files, including the research-only worker receipt fixture,
and continues to return `decision=HOLD`. No model traffic or
agent execution is enabled.


## V2 atomic synthetic debit and outbox intent (2026-10-19)

See [ATOMIC_SYNTHETIC_OUTBOX_REVIEW.md](ATOMIC_SYNTHETIC_OUTBOX_REVIEW.md).
`atomic_synthetic_lease_outbox.py` is an explicitly opt-in **V2**
version of the research-only SQLite lease broker. A lease debit and its
HMAC-authenticated outbox intent are inserted into **one SQLite
transaction**. The old V1 and new V2 adapters refuse each other's
database schema; no migration or production activation occurred.

Focused unit and disposable subprocess tests passed on x1-370,
Lenovo and Destroyer. Injected failures both before and after
outbox insertion rolled back the debit. Restarted processes retained
the outbox and rejected duplicate operations. An old valid database
paired with an old trusted head remains a rollback vulnerability.
External observer publication and worker ACK are NOT part of this
atomic transaction, and `agent_dispatch_authorized=false`.

The same-host Fleet Commander receipt now checks **14** evidence
categories. Lenovo's physical report remains `decision=HOLD`;
all live provider, model, sandbox and runtime admission paths are
disabled.


## V2 signed outbox publication / ACK / delivery fixture (2026-10-20)

See [V2_OUTBOX_PUBLICATION_HANDOFF.md](V2_OUTBOX_PUBLICATION_HANDOFF.md).
The V2 outbox now supports read-only export of one fully locally
verified event at a caller-provided exact head. New
`v2_outbox_publication.py` defines narrowly scoped Ed25519 worker
publication, observer ACK, and worker delivery records. The observer
refuses to sign before verifying the exact worker operation/head/state;
the worker refuses to sign delivery before checking the observer ACK.

Disposable x1-370↔Lenovo physical tests PASS five scenarios: valid
exchange, missing ACK, tampered publication, tampered ACK and tampered
delivery. **No live inference, agent dispatch or provider calls.**
The exchanged public keys are self-introduced over the same SSH
test session: independent signer custody, durable source authority,
cross-restart fencing and external high-water are NOT established.
The 14-source Lenovo same-host admission receipt remains HOLD.


## V2 restart-durable observer inbox and untrusted-claim quarantine (2026-10-20)

See [V2_DURABLE_OBSERVER_INBOX_HANDOFF.md](V2_DURABLE_OBSERVER_INBOX_HANDOFF.md).
`v2_durable_observer_inbox.py` introduces a **separate private SQLite
inbox** that persists one verified V2 worker publication, its signed
observer ACK and later worker delivery with observer-signed stage
markers. Uncertain publication and signed-but-undelivered ACKs remain
fenced across cooperative observer restarts. A worker's unverified
claim can be **durably quarantined** without authorizing an ACK,
retry or refund.

The short-lived subprocess fixture passed on x1-370, Lenovo and
Destroyer. Five strict-host-key x1↔Lenovo scenarios also passed with
`two_node_v2_outbox_probe.py --durable-inbox`, including invalid
publication quarantine. The release qualification receipt now has
**15 same-host evidence inputs** and remains `decision=HOLD`.

An old valid observer SQLite image **plus its old claimed trusted
anchor is still accepted**. The identity keys, external monotonic
high-water and source-attested worker transactions are NOT separately
custodied or verified; the pilot continues to forbid real agent
dispatch, model egress or any production deployment.


## Physically off-host signed V2 inbox stage witness (2026-10-20)

See [V2_OFFHOST_MARKER_WITNESS_HANDOFF.md](V2_OFFHOST_MARKER_WITNESS_HANDOFF.md).
A research-only `v2_offhost_marker_witness.py` verifies observer-signed
V2 inbox stage markers and records monotonic transitions in a private
JSONL journal. It requires exact latest anchors and refuses stage
skips, forged signatures, stale appenders and changed signed heads.

Fleet Commander exercised the observer on x1-370 with the journal on
Destroyer using new short-lived, strict-known-host SSH processes for
each stage. An **actual restored older x1 SQLite image** was rejected
when compared with Destroyer's still-newer witness. A rolled-back
witness log was also denied against the retained newer anchor;
the valid older witness **plus older anchor** rollback vulnerability
was deliberately reproduced. The entire Destroyer scratch folder
was cleaned after testing.

**This is physically separated research evidence only.** Observer
public keys and heads remain controlled by the same operator. The
witness has no independently administered signer, source-attested
transaction ID or monotonic immutable custody. It does not affect
Lenovo's separate 15-source same-host HOLD decision and enables no
agent dispatch, inference, or production deployment.


## Signed receipts from the separate V2 witness host (2026-10-20)

See [V2_COUNTERSIGNED_OFFHOST_RECEIPTS_HANDOFF.md](V2_COUNTERSIGNED_OFFHOST_RECEIPTS_HANDOFF.md).
`v2_witness_countersign.py` adds an ephemeral **Destroyer-generated
Ed25519 witness signing key** in an owner-private disposable
fixture directory. After verifying a signed V2 observer marker
and an exact off-host witness head, a fresh SSH process signs a
narrowly scoped acceptance receipt that x1-370 verifies against
the originally bootstrapped witness public key.

Reopening the same disposable key after process restart reconstructs
the **same** deterministic receipt on read-only retry; stale or
tampered receipts, wrong signer, stale witness writes and forged
dispatch status are refused. The physical test actually restores an
older valid witness log and proves retained newer heads reject it,
while the old valid log **plus old anchor still succeeds**.
All host scratch state and the test signing key are removed after
the research run.

**Role separation is not independent administrator custody.**
Signer bootstrap and key-pin exchange are on the same operator's SSH
channel. No immutable external high-water, source transaction
attestation, live inference, runtime admission or production release
is established. Lenovo's separate 15-gate release HOLD is unchanged.


## Fourth-role detached custody checkpoint verification (2026-10-20)

See [V2_EXTERNAL_CUSTODY_CONTRACT_HANDOFF.md](V2_EXTERNAL_CUSTODY_CONTRACT_HANDOFF.md).
The research-only `v2_custody_checkpoint_contract.py` requires an
explicitly supplied, separately pinned custodian Ed25519 public key,
exact current signed checkpoint SHA-256, previous checkpoint digest,
witness/observer heads, public-key fingerprints, trusted time and
monotonic witness-count floor. It rejects missing or stale inputs,
forged witness receipts, signer substitutions and claimed model
authority; every valid research result still says HOLD.

Fleet Commander tested a disposable fourth signing role on **Lenovo**
alongside x1-370's synthetic worker/observer and **Destroyer's**
witness signer. A new Lenovo process reproduced the same signed
checkpoint, and a generation-2 checkpoint rejected the older stage
when the newer external floor was supplied. **The original old
checkpoint also passed when the old floor was supplied**: mandatory
counterexample, not rollback resistance.

All four keys are generated under the SAME operator's administration
in this trial, and the expected checkpoint/floor comes from the same
x1 research harness. No independent administrator, immutable external
high-water, resource-side transaction authority, agent model access,
admission, or production deployment was established. Lenovo's
separate 15-source admission receipt remains HOLD.


## V2 synthetic custody signing ledger + local revocation (2026-10-20)

See [V2_DURABLE_CUSTODY_LEDGER_HANDOFF.md](V2_DURABLE_CUSTODY_LEDGER_HANDOFF.md).
Lenovo's disposable research signer now creates an owner-private
**SQLite checkpoint/signature journal** with immutable-by-local-policy
four-role public roots, complete signed-receipt re-verification,
sequential generation/previous-digest checks, strictly advancing
witness/observer heads, and durable local terminal revocation.
A repeated `sign` is denied; a lost response uses a separate,
read-only `readback` with exact generation/heads/scope.

Fleet Commander physically exercised the x1-370→Destroyer→Lenovo
chain, including an actual earlier SQLite image rollback, a new
generation after receipt-bound ACK, revocation that persists across
separate processes, and refusal of a valid new signature after
revocation. **Old ledger + old head** or **unrevoked old SQLite image**
are still accepted under shared admin control; this is NOT external
rollback resistance or independently administered revocation.
All fixtures are disposable and no real model/agent access is allowed.


## Signed offline research revocation authority state (2026-10-20)

See [V2_EXTERNAL_REVOCATION_AUTHORITY_HANDOFF.md](V2_EXTERNAL_REVOCATION_AUTHORITY_HANDOFF.md).
The new `v2_external_authority_state.py` requires a **fifth**
Ed25519 role and exact independently *supplied* authority public key,
signed-state SHA-256, predecessor, scope, sequence floor and clock.
It validates synthetic `checkpoint_recorded` and
`checkpoint_revoked` states but never authorizes any agent/model.
A retained newer signed revocation rejects a checkpoint recovered
from an **actually restored unrevoked Lenovo SQLite image**.

The x1-370/Destroyer/Lenovo physical test passed. All five signing
roles remain under **the same fleet operator's control**, however;
the authority key is temporary on x1, not third-party provisioned.
Rolling back BOTH the authority state and its claimed expected head
re-enables acceptance of the old signed checkpoint, as the mandatory
negative test confirms. This is *conditional* revocation verification,
not independent immutable custody or a production admission gate.


## Signed synthetic resource-side commit (2026-10-20)

See [V2_SOURCE_TRANSACTION_BOUNDARY_HANDOFF.md](V2_SOURCE_TRANSACTION_BOUNDARY_HANDOFF.md).
Destroyer now has an opt-in, **disposable two-unit synthetic resource**
fixture. Within ONE source-local SQLite transaction it commits a
balance decrement and an Ed25519-signed receipt. The separate x1
synthetic worker intent and Destroyer receipt are both signature-
verified and must agree on operation ID and request SHA-256.

Fleet Commander physically exercised fresh-process source readback,
duplicate/replay/forgery refusal, two-unit exhaustion, and restoration
of an actual old Destroyer SQLite image. The older image is rejected
against a retained newer head, but **old image + old claimed head**
still succeeds. SQL-fault injection also proved local rollback of
both receipt and balance when a commit fails.

The source resource is **synthetic only**, under the same operator's
SSH administration. Source and worker SQLite transactions are
not distributed-atomic; no real model, provider, repository effect,
independent resource custody or agent dispatch is proven. The Lenovo
15-source admission receipt remains HOLD.


## V2 lost-response crash-window recovery (2026-10-20)

See [V2_SOURCE_CRASH_PARTITION_RECOVERY_HANDOFF.md](V2_SOURCE_CRASH_PARTITION_RECOVERY_HANDOFF.md).
The disposable Destroyer source now supports **read-only**
`recover` of the exact next source-signed receipt from the
caller-held prior head, operation ID and original worker-intent
SHA-256. `v2_cross_host_uncertainty.py` distinguishes
`SOURCE_LOCAL_COMMITTED_HOLD`, `UNCERTAIN_HOLD` (missing or
unreachable source), and `QUARANTINED_HOLD` (untrusted evidence),
with **no automatic retry, refund, unfence, model or agent grant**.

Fleet Commander physically killed only two disposable source
test processes: one **before** source commit (no effect) and one
**after** source commit but **before** returning a response
(effect recovered by fresh SSH/Python read-only lookup). Both
cases keep distributed transaction authority on HOLD.
An old source DB with its old head still passes the mandatory
rollback counterexample. These are synthetic, process-level
faults, not actual fleet power cuts or independently custodied
production transaction proofs.


## Durable worker-side HOLD journal after source failure (2026-10-20)

See [V2_DURABLE_WORKER_UNCERTAINTY_HANDOFF.md](V2_DURABLE_WORKER_UNCERTAINTY_HANDOFF.md).
The optional disposable `v2_uncertainty_journal.py` stores the
original worker-signed intent plus bounded hash-linked recovered
source evidence. Every reopened record is checked against worker
and source signatures, pinned keys, original source head and
prior journal hash. States remain `UNCERTAIN_HOLD`,
`QUARANTINED_HOLD` or `SOURCE_LOCAL_COMMITTED_HOLD`—never
retry, refund, agent dispatch or model egress.

Fleet Commander exercised before/after source-commit process
crashes and a **fresh x1 Python-process readback** of the signed
source evidence after recovery. Restoring an older local journal
is rejected only with a newer retained external head;
**old journal + old head** remains a same-admin rollback attack.
Separate worker/source SQLite transactions are NOT globally atomic.
