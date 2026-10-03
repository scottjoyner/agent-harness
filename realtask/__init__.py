"""Deterministic real-task benchmark + bounded swarm experiment harness.

Models are read-only participants. They receive bounded source text and return
structured text. The harness owns every mutation: patch application happens only
inside a disposable evaluation worktree, acceptance commands come from a
fixture-declared allow list, and the authoritative source tree is never opened
for writing.

This package is independent of production scheduling. It does not claim
AssistX tasks, complete tasks, register providers, project runtime, dispatch
work, or push to any benchmark target repository.
"""
from __future__ import annotations

from .taxonomy import Outcome  # noqa: F401
from .version import (  # noqa: F401
    MAX_REFINEMENTS,
    SCHEMA_COMPARISON,
    SCHEMA_METRICS,
    SCHEMA_ROLLUP,
    SCHEMA_RUN_MANIFEST,
    SCHEMA_TASK,
)

__all__ = [
    "Outcome",
    "MAX_REFINEMENTS",
    "SCHEMA_TASK",
    "SCHEMA_RUN_MANIFEST",
    "SCHEMA_METRICS",
    "SCHEMA_COMPARISON",
    "SCHEMA_ROLLUP",
]
