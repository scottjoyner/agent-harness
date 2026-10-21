# Restricted Hermes: physical Docker Compose startup qualification

Date: October 21, 2026 — x1-370, agent-harness research draft PR #18.
Decision: **HOLD — no production cutover or live Hermes dispatch authorized.**

## Verified problem

Read-only Docker inspect of the actual running `assistx-hermes-adapter`
shows **16 mounts, 9 writable**. The overlapping writable aliases
`/home/scott/knowledge` (resolves to SSD knowledge) and
`/media/scott/SSD_4TB` permit broad container-level reachability of
protected research evidence, subject to process permissions. These
mounts were NOT changed. The production container remains running on
image `sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307`.

Other observed broad writable surfaces include the NAS5 root, a
mutable mounted app source directory, four Git checkouts, and writable
Hermes home. A future restricted production design must individually
justify legitimate writes without inheriting the broad ancestors.

## Exact research commands

Safe dry run, zero Docker side effects:

~~~sh
python3 -B pilot/omnigent_herdr/assistx_hermes_compose_physical_canary.py --json
~~~

Opt-in synthetic Compose deployment ONLY on x1-370, as nonroot:

~~~sh
python3 -B pilot/omnigent_herdr/assistx_hermes_compose_physical_canary.py --physical-disposable-compose --json
python3 -B -m unittest discover -s pilot/omnigent_herdr -p 'test_*.py'
~~~

## What the disposable physical evidence proves

1. The image ID was read from the original running production container,
   and used as an exact, no-pull base (not a source attestation).
2. Two offline timestamp-controlled rootless overlay builds were identical,
   with local image ID
   `sha256:9d5c1894398c0d62943f6aa5eb2dddc3d5b6d808138b2b02bbd84fdb66c0aa23`.
3. The already-reviewed, explicit-profile synthetic Compose YAML was
   rendered with `docker compose config --format json` and accepted by
   the strict Compose allowlist. No real SSD/NAS/repo/SSH mount path appeared.
4. `docker compose create --no-build --pull never` created one
   short-lived container in a freshly chosen isolated project.
   The Docker service/project labels, image ID, effective container
   security settings and exact two mounts were independently inspected
   before the container started.
5. The worker was started from the normal candidate rootless
   `/entrypoint.sh`, running actual image-baked
   `assistx.strict_executor_main` with a test-only synthetic HTTP
   poll interceptor. Network mode was `none`; no model/provider calls
   or real owner-authorized tasks were executed.
6. Nonroot UID, read-only rootfs, synthetic RO knowledge mount, narrowly
   writable synthetic scratch workspace, Linux cap-drop, no-new-privileges,
   process/memory/CPU limits and no credential `.env` generation were
   physically checked. The fixture denies write attempts to synthetic
   RO knowledge and rootfs and verifies the rootless home configuration.
7. The exact test container, disposable image tags and temporary scratch
   were removed. The original production Hermes container remained
   running with the same image ID throughout the test.
8. Evidence decision is explicitly HOLD even if every test passes.

## Required migration and release review

The existing adapter's legitimate **READ** dependencies need an
allowlisted, source-provenance-bound knowledge subtree. Current source
defaults reference `HERMES_KNOWLEDGE_ROOT` and `HERMES_EVAL_PATH`,
which can be explicitly relocated to scratch in a restricted candidate.
Legitimate runtime **WRITES** may include Hermes sessions, config, logs,
cache, evaluation results and output artifacts. The restricted
rootless candidate uses `/workspace/hermes-home` and a writable
`/workspace` fixture for those paths, but has not yet proven full
production ingestion and research compatibility.

The real production deploy must additionally resolve or remove:

- Both broad RW aliases enclosing the SSD protected evidence corpus.
- RW NAS5 root and other ancestor or symlink write bypasses.
- Mutable app source and Git checkout RW mounts.
- Real SSH private-key access and any credential serialization; neither
  was admitted into this synthetic candidate.
- Genuine source discovery, ingestion, retries, restart, offline
  behavior and output custody under an exact reviewed build.
- Independent signed image provenance, real owner authorization,
  independently controlled revocation/fencing and rollback evidence.

No production Compose configuration was edited; no service restart,
network exposure, task dispatch or credential change was attempted.
The next safe acceptance gate is **source-path-specific allowlisting**
and disposable real-code, synthetic-input ingestion/restart testing,
plus independent review. Issue: auto-assist #414 remains OPEN.
