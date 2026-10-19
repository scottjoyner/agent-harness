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
