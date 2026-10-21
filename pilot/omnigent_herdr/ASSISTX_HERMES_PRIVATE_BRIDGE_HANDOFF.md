# Hermes cross-container private bridge qualification

Date: October 21, 2026. Scope: agent-harness draft PR #18 on x1-370.
**HOLD. Do not use these fixtures in production.**

## Commands

No-Docker inspection (HOLD report):

~~~sh
python3 pilot/omnigent_herdr/assistx_hermes_private_network_canary.py --json
~~~

Explicit disposable physical test only on x1-370 as a nonroot user:

~~~sh
python3 pilot/omnigent_herdr/assistx_hermes_private_network_canary.py --physical-disposable-private-network --json
python3 -m unittest discover -s pilot/omnigent_herdr -p 'test_*.py'
~~~

## Verified test architecture

* Uses the exact original running Hermes image ID:
  sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307.
* Two reproducible offline rootless overlay builds:
  sha256:9d5c1894398c0d62943f6aa5eb2dddc3d5b6d808138b2b02bbd84fdb66c0aa23.
* Creates one random private Docker **internal** bridge network.
  No public or host ports are published. Network inspect verifies
  Internal=true, Driver=bridge, Attachable=false and no foreign endpoints.
* Creates exactly two disposable containers: a synthetic fake AssistX
  API and a Hermes worker. Both are UID 1000, read-only rootfs, Linux
  capabilities dropped, no-new-privileges, strict resource limits, and
  only a synthetic read-only knowledge mount plus one writable test
  workspace each. No production SSD/NAS/SSH/API bind mounts.
* The worker runs the **normal rootless entrypoint** and real
  image-baked strict_executor_main with a *synthetic-only*
  sitecustomize interception of Hermes executable/model execution.
* Initial poll/claim/task-token/heartbeat/context/memory/DONE requests
  cross the private Docker bridge as real HTTP to the fake API.
* A second synthetic task is claimed and its scoped token recorded
  only inside the worker's ephemeral scratch directory.
* The operator wrapper stops the **fake API container only**. The next
  fresh worker process sees connection failures; no completion is sent.
* On synthetic API restart, the original unexpired lease remains held.
  A synthetic logical-clock advance expires the lease, permitting a
  new claim with a different scoped token and claim identifier.
* Both the previous token and previous claim identifier fail HTTP 409.
  A replay of a newly completed task also fails HTTP 409.
* End state is DONE/DONE/LOCKED for synthetic task IDs A/B/C.
* Two containers, the private network, the temporary tags and all
  synthetic scratch files are removed after the run.
* Zero real task execution and zero provider requests were observed.
  The production Hermes container remained running on its prior image.

## Limitations and release decision

Cross-container networking is real but **both containers are controlled
by the same host operator and Docker daemon**. Internal bridge and
short-lived random names are not proof of independently administered
server identity, rollback-resistant memory, cross-host timing, physical
network partition, malicious concurrent-worker claims or durable fencing.
Synthetic tokens/lease clock are fixtures, not production credentials.

Production Hermes still exposes broad writable SSD/knowledge ancestor
mounts: AssistX issue #414 remains open. Independent source/image signing,
separately controlled revocation/fencing, actual owner-approved task
receipts, independent review, live workload compatibility and audited
rollback are missing. Do not remove the production service or deploy
this canary as a release. Decision: **HOLD**.

Next safe step: investigate host-separated fake API authority and concurrent
worker claims, then produce independently signed evidence for both
mount-remediation tests and production rollout approval.
