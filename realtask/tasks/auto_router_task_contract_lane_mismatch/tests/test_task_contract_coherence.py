"""Targeted acceptance for the ``auto_router_task_contract_lane_mismatch`` fixture.

``build_task_contract`` is supposed to emit one coherent description of a task.
At the frozen revision it emits contracts that contradict themselves: a
``refinement`` task is handed the code-execution plan ("Make the smallest safe
change") and the code validation metrics (``regressions_checked``) while
simultaneously being classified ``requires_tools=False`` /
``capability_lane="prompt_only"``.

The invariant under test is *coherence*, not a specific list of kinds: whenever
the contract prescribes a plan or a metric that only makes sense with tool
access, it must also declare that tool access.

No stubs are needed. The module imports ``typing`` only, and loads from the
evaluation worktree.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
MODULE_PATH = WORKTREE / "auto_router" / "task_contract.py"

#: Metrics that can only be produced by something that can touch the world.
TOOL_IMPLYING_METRICS = frozenset({
    "regressions_checked",
    "state_verified",
    "change_applied",
    "health_confirmed",
})

#: Plan steps that only make sense with tool access.
TOOL_IMPLYING_STEPS = (
    "Make the smallest safe change",
    "Apply the smallest safe operation",
)

#: Every kind the frozen revision gives a non-generic plan. Each of these must
#: therefore also be classified as needing tools.
EXPLICIT_PLAN_KINDS = (
    "code", "implementation", "refinement", "repair", "review", "repo", "patch",
    "research", "analysis", "documentation", "docs",
    "operations", "terminal", "shell",
)


@pytest.fixture(scope="module")
def contract():
    spec = importlib.util.spec_from_file_location("task_contract", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["task_contract"] = module
    spec.loader.exec_module(module)
    return module


def test_module_loads(contract):
    assert hasattr(contract, "build_task_contract")
    assert hasattr(contract, "normalize_task_kind")


def test_coding_normalizes_to_code(contract):
    assert contract.normalize_task_kind({"task_kind": "coding"}) == "code"


@pytest.mark.parametrize("kind", EXPLICIT_PLAN_KINDS)
def test_explicit_plan_kinds_are_classified_as_needing_tools(contract, kind):
    """The core defect: these kinds get code/ops plan steps but are not lane-tagged."""
    built = contract.build_task_contract({"task_kind": kind})
    assert built["task_kind"] == kind
    assert built["requires_tools"] is True, (
        "{} receives an explicit plan and metrics but is classified "
        "prompt_only: {!r}".format(kind, built)
    )
    assert built["capability_lane"] == "tool_required", (kind, built)
    assert built["workflow_stage"] == "iterative", (kind, built)


@pytest.mark.parametrize("kind", ["refinement", "repair", "review", "repo", "patch"])
def test_code_kinds_receive_code_metrics_and_are_not_prompt_only(contract, kind):
    built = contract.build_task_contract({"task_kind": kind})
    assert set(built["validation_metrics"]) & TOOL_IMPLYING_METRICS, (kind, built)
    assert built["requires_tools"] is True, (kind, built)


@pytest.mark.parametrize("kind", ["documentation", "docs", "terminal", "shell"])
def test_other_explicit_plan_kinds_are_also_coherent(contract, kind):
    built = contract.build_task_contract({"task_kind": kind})
    assert built["requires_tools"] is True, (kind, built)
    assert built["capability_lane"] == "tool_required", (kind, built)


@pytest.mark.parametrize(
    "kind",
    ["code", "implementation", "refinement", "repair", "review", "repo", "patch",
     "research", "analysis", "documentation", "docs", "operations", "terminal", "shell"],
)
def test_plan_steps_implying_tools_imply_the_tool_lane(contract, kind):
    built = contract.build_task_contract({"task_kind": kind})
    if any(step.startswith(TOOL_IMPLYING_STEPS) for step in built["plan_steps"]):
        assert built["requires_tools"] is True, (
            "{} is told to change or operate something but is lane-tagged "
            "prompt_only".format(kind)
        )


def test_lane_and_requires_tools_never_disagree(contract):
    """capability_lane is derived from requires_tools; the two must not diverge."""
    for kind in EXPLICIT_PLAN_KINDS + ("general", "unknown_kind"):
        built = contract.build_task_contract({"task_kind": kind})
        expected = "tool_required" if built["requires_tools"] else "prompt_only"
        assert built["capability_lane"] == expected, (kind, built)


def test_workflow_stage_matches_the_lane(contract):
    """iterative means the task is executed against live state, not just answered."""
    for kind in EXPLICIT_PLAN_KINDS:
        built = contract.build_task_contract({"task_kind": kind})
        assert built["workflow_stage"] == "iterative", (kind, built)


def test_no_kind_receives_an_explicit_plan_without_the_tool_lane(contract):
    """Sweep every kind the module itself knows how to classify."""
    kinds = set()
    for kind in EXPLICIT_PLAN_KINDS + ("general",):
        kinds.add(kind)
    offenders = []
    for kind in sorted(kinds):
        built = contract.build_task_contract({"task_kind": kind})
        explicit_plan = built["plan_steps"] != [
            "Clarify the immediate goal.",
            "Advance the task in one small step.",
            "Validate the result.",
            "Summarize the next handoff.",
        ]
        if explicit_plan and not built["requires_tools"]:
            offenders.append((kind, built))
    assert not offenders, offenders


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
