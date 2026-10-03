"""Schema identifiers and provenance markers for the real-task benchmark.

Every artifact written by this harness carries one of these ``SCHEMA_*`` strings
so that evidence produced here is never confused with the historical benchmark
artifacts already committed to this repository (``bench_*.json``,
``bench_v*.json``, ``dual_*.json``, ``cpm_tb2_*.json``, ``benchmark_*.json``).

See ``docs/EVIDENCE-PROVENANCE.md`` for the mapping between the legacy
artifacts and this schema family.
"""

HARNESS_NAME = "realtask"

SCHEMA_RUN_MANIFEST = "realtask.run_manifest.v1"
SCHEMA_TASK = "realtask.task.v1"
SCHEMA_SOURCE_MANIFEST = "realtask.source_manifest.v1"
SCHEMA_ROLE_RESULT = "realtask.role_result.v1"
SCHEMA_TEST_RESULTS = "realtask.test_results.v1"
SCHEMA_METRICS = "realtask.metrics.v1"
SCHEMA_COMPARISON = "realtask.comparison.v1"

TASK_SCHEMA_VERSION = 1

#: Hard ceiling on swarm refinement rounds. A single attempt may never perform
#: more than this many implement-after-review cycles.
MAX_REFINEMENTS = 1
