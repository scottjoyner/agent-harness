# Synthetic checkpoint witness log — Fleet Commander handoff

**October 19, 2026. Research-only. Live models, agent dispatch, and production deployment remain HOLD.**

This slice introduces a *separate* Ed25519-signed, hash-linked,
cooperatively append-only checkpoint log. The log resides outside the
worker's synthetic lease SQLite database and observer's experimental ACK
SQLite journal. That physical/logical separation is **not equivalent to
independent administrative custody**. A shared operator/SSH credential
can still replace both the log and its trusted latest anchor.

## New components

- `checkpoint_witness_log.py` contains a small bounded JSON-lines log
  supporting genesis, a single sequential append, read-only verification,
  and externally supplied `(generation, SHA-256-of-signed-record)` anchors.
- `test_checkpoint_witness_log.py` checks successful chain validation,
  failures on altered signatures, changed bytes, duplicate or noncanonical
  JSON, truncated tails, missing or stale anchors, wrong checkpoint key,
  missing/skipped generations, symlink/hardlinked/world-readable file,
  and concurrent stale writers.
- `two_node_witness_probe.py` runs the observer on x1-370 and reuses
  `observer_remote_worker_fixture.py` on Lenovo, using one bounded,
  strict-host-key SSH session. Both the local witness log and worker's
  SQLite DB are synthetic and disposed after the run.

The witness file is a newly created, private regular 0600 file in a
private 0700 directory, bounded to 4 MiB and at most 2,048 records.
Each record signs its generation, previous signed-record hash, entire
signed checkpoint and pinned observer public-key fingerprint using
a versioned Ed25519 domain separator. Each checkpoint also independently
binds the observer and worker role names, its predecessor checkpoint
hash, current state digest and generation.

The cooperative publisher obtains an exclusive advisory file lock,
validates the full log, requires its external anchor to equal the **actual
current head**, writes and fsyncs one line, revalidates, then returns a new
head to the caller. Creation fsyncs the parent directory as well.
A verifier opens the file **read-only**, obtains a shared lock and
checks every record before accepting a caller's pinned anchor.
An existing historical anchor is sufficient for *inspection* but not
publication. An old writer anchor is never silently rolled forward,
replayed or repaired: it is fenced for reconciliation.

**Failure semantics:** If log append reaches disk and then delivery of the
new head is lost, a caller presenting its old head can inspect history,
but cannot publish a new transition. This is deliberate. An independent
operator must establish the right head before resuming. There is no
automatic refund, rollback, new signing key, or forced unfence.

## Physical evidence (synthetic only)

- x1-370: 24 private-file witness unit tests **PASS**; direct
  x1-370→Lenovo SSH witness experiment **PASS**.
- Lenovo: the same 24 witness tests **PASS** with private disposable
  files, without loading models or changing AppArmor.
- Destroyer: the same 24 witness tests **PASS**; all temporary research
  Python files were removed after testing. Existing workloads untouched.

The x1-370→Lenovo experiment confirmed two signed sequential checkpoints,
a current signed head matching post-debit remote synthetic SQLite state,
and rejection of the worker's restored old state. It also restored a
previous valid *x1* witness log file: the **newer in-RAM pinned anchor**
detected the rollback. A deliberately stale writer anchor was rejected.
The final mandatory negative control restored both the old log and
its old anchor, which **still appeared valid**.

All Ed25519 signing material stayed within the x1-370 fixture process;
Lenovo generated its own separate in-RAM worker HMAC key. Neither private
key crossed SSH or was stored on disk. The observer's source of worker
state remains the worker's own SSH snapshot response: no independent
resource-side attestation is proven.

## Remaining production blockers

This append-only *software discipline* is not a cryptographically
immutable data store. The observation log, public-key pin, and external
anchor lack separately administered physical custody. Restoring an old
log AND the trusted anchor defeats the prototype. A malicious same-UID
or privileged filesystem writer may also ignore advisory locking or
replace files; arbitrary power loss, storage firmware replay and split
brain have not been qualified.

It does not verify an independently signed ACK from the real worker,
provide permanent observer signing-key custody, continuously publish
every accepted transaction, enforce provider quotas, or authorize model
traffic. There is no connection to live Herdr, Omnigent, AssistX,
OpenCode, Sophia, Hermes, Auto-Router, or any production system.

**Smallest safe next work:** approve an *independently administered*
immutable/high-water channel and distinct trust roots, bind each worker
SQLite transaction and its resource-side signed ACK to a witness record,
and test crash at every append/fsync/ACK boundary. A standalone witness
record must never release a fenced operation. Obtain operator approval
for Lenovo's AppArmor vendor profile and re-qualify its memory headroom
separately. The requested OpenCode free DeepSeek pin and reviewer
independence remain unresolved.

**No production writes, real model requests, protected merges, AppArmor
edits, public tunnels, or existing-process terminations.**
