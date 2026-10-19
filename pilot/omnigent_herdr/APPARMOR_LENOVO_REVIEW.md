# Lenovo Bubblewrap AppArmor change — operator review only

**Status: candidate-qualified, NOT installed or authorized.** The global
Ubuntu 24.04 restriction is currently enabled
(`kernel.apparmor_restrict_unprivileged_userns=1`), but non-root Bubblewrap
fails to create the required namespaces. Never disable the global restriction,
use `aa-complain`, give Bubblewrap extra POSIX capabilities, or run agents
outside an enforcing namespace sandbox.

## Evidence collected through Fleet Commander

| Evidence | Value |
| --- | --- |
| Host | `scott-Lenovo-ideapad-330S-15IKB` |
| Kernel | `6.8.0-41-generic` |
| AppArmor parser | `4.0.1` |
| Source | Ubuntu signed-package-index `apparmor-profiles` |
| Package version | `4.0.1really4.0.1-0ubuntu0.24.04.9` |
| Extracted .deb SHA-256 | `90b02aa006eea7702cd4e851343e469e41365dda42145a3cb035de1d6c773b8c` |
| Vendor profile | `usr/share/apparmor/extra-profiles/bwrap-userns-restrict` |
| Profile SHA-256 | `11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9` |
| `apparmor_parser -Q -T` | Exit 0 — offline syntax check only |
| Existing /etc AppArmor profile | None found for bwrap |
| Noninteractive administrative permission | Not available |

The vendor profile attaches to **/usr/bin/bwrap system-wide**, not solely to
the pilot. It grants Bubblewrap namespace and mount operations and transitions
children to a stacked `unpriv_bwrap` profile that audits/denies capabilities.
This is an audited candidate, **not** a proof of kernel enforcement or complete
isolation. A security administrator must review the scope and the original
profile, confirm no competing profiles, and approve any host mutation.

Source: [Ubuntu 24.04 release notes — unprivileged user namespaces](https://documentation.ubuntu.com/release-notes/24.04/)
and `apparmor-profiles` package contents. Do **not** substitute an
`flags=(unconfined)` generic profile: it grants a broader exception.

## Reproducible no-root inspection (already performed)

The downloaded Ubuntu package and extracted profile are in the local
user-scoped pilot work directory, not under `/etc`. Running the probe does
not change any kernel policy:

```bash
ROOT="$HOME/.local/share/herdr-omnigent-lenovo-pilot"
CANDIDATE="$ROOT/work/policy-research/unpacked/usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
sha256sum "$CANDIDATE"
python3 pilot/omnigent_herdr/apparmor_vendor_probe.py "$CANDIDATE"
# candidate_qualified may be true; profile_loaded and authorization remain false
```

If the package version/hash changes, STOP and require a new review. The parser
result means syntax compatibility, not permission to load the profile.

## Proposed one-host rollout (requires explicit administrator approval)

Only after a security review and change authorization:

1. Confirm that `/etc/apparmor.d/bwrap-userns-restrict` does not already exist,
   that no `bwrap` profile is loaded, and that no existing fleet workloads
   depend on an alternative Bubblewrap policy.
2. Verify the pinned SHA-256 of the extracted candidate immediately before
   installing. Copy it as a root-owned `0644` file under
   `/etc/apparmor.d/bwrap-userns-restrict`; verify its SHA-256 again.
3. As administrator, run an offline syntax check on the installed file,
   then `apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict` to load
   only this profile. Do not reload unrelated services or change sysctls.
4. Verify the kernel restriction still reads `1`. Run
   `python3 sandbox_probe.py` on Lenovo. Required evidence: sandbox can
   write only in the designated worktree, cannot alter the out-of-scope
   sentinel, and cannot connect to an external network. Also re-check
   launcher argument construction; the sandbox may be bypassed if a worker
   executes outside the wrapped process.
5. If any check fails, stop any new Bubblewrap pilot processes, remove the
   profile from the kernel using `apparmor_parser -R`, remove only the
   change-created policy file, and verify that the original namespace denial
   is restored. Preserve read-only rejection and rollback receipts.

Do not run Freebuff, authenticate agents, enable public tunnels, merge the
draft PR, or deploy to production during the namespace change. The subsequent
credential/model-pin/quota/independent-review gates are separate.

## Operator decision

**HOLD.** Only the pinned vendor profile has passed an offline parse and
checksum. The administrator has not approved a system security policy change.
The current `sandbox_probe.py` rejects Lenovo and `preflight.py` must keep
`dispatch_ready=false`.
