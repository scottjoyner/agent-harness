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
