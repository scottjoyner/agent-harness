"""Broader acceptance for the ``auto_router_task_contract_lane_mismatch`` fixture.

Guards the rest of the contract module, so that a repair which fixes the lane
mismatch cannot quietly break the precedence rules that callers depend on.

The trap this catches: the obvious fix is to widen the ``requires_tools`` kind
set. A lazier fix is to delete or reorder the explicit-override short-circuit so
that kind inference always wins. That would make every test here fail, because a
caller who explicitly said ``requires_tools: false`` would be overruled.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
MODULE_PATH = WORKTREE / "auto_router" / "task_contract.py"


@pytest.fixture(scope="module")
def contract():
    spec = importlib.util.spec_from_file_location("task_contract_broad", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["task_contract_broad"] = module
    spec.loader.exec_module(module)
    return module


# -- explicit overrides must still win ------------------------------------

def test_explicit_requires_tools_false_is_honoured(contract):
    built = contract.build_task_contract(
        {"task_kind": "code", "requires_tools": False}
    )
    assert built["requires_tools"] is False
    assert built["capability_lane"] == "prompt_only"


def test_explicit_requires_tools_true_is_honoured_for_a_prompt_kind(contract):
    built = contract.build_task_contract(
        {"task_kind": "general", "requires_tools": True}
    )
    assert built["requires_tools"] is True
    assert built["capability_lane"] == "tool_required"


def test_metadata_override_is_honoured(contract):
    built = contract.build_task_contract(
        {"task": "please tidy this up", "metadata": {"requires_tools": False}}
    )
    assert built["requires_tools"] is False


def test_explicit_capability_lane_is_honoured(contract):
    built = contract.build_task_contract(
        {"task_kind": "code", "capability_lane": "manual_review"}
    )
    assert built["capability_lane"] == "manual_review"


# -- inference paths that short-circuit before kind inference -------------

def test_finalized_flag_forces_the_tool_lane(contract):
    built = contract.build_task_contract({"task_kind": "general", "finalized": True})
    assert built["requires_tools"] is True
    assert built["workflow_stage"] == "handoff"


def test_reviewed_flag_forces_the_tool_lane(contract):
    built = contract.build_task_contract({"task_kind": "general", "reviewed": True})
    assert built["requires_tools"] is True


@pytest.mark.parametrize("stage", ["handoff", "final", "finalized", "review_final"])
def test_terminal_workflow_stages_force_the_tool_lane(contract, stage):
    built = contract.build_task_contract({"task_kind": "general", "stage": stage})
    assert built["requires_tools"] is True


def test_non_terminal_workflow_stage_does_not_force_the_tool_lane(contract):
    built = contract.build_task_contract({"task_kind": "general", "stage": "draft"})
    assert built["requires_tools"] is False


# -- keyword inference ----------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("investigate the provider error and cite sources", "research"),
        ("measure throughput and compare the options", "analysis"),
        ("refine the wording and edit the summary", "refinement"),
        ("deploy the service and monitor health", "operations"),
        ("fix the bug in the parser and add a test", "code"),
    ],
)
def test_keyword_inference_is_unchanged(contract, text, expected):
    assert contract.normalize_task_kind({"task": text}) == expected


def test_unclassifiable_text_falls_back_to_general(contract):
    assert contract.normalize_task_kind({"task": "hello there"}) == "general"


def test_coding_alias_normalises_to_code(contract):
    assert contract.normalize_task_kind({"task_kind": "Coding"}) == "code"
    # "kind"/"category" are read from metadata, not from the payload root.
    assert contract.normalize_task_kind({"metadata": {"kind": " coding "}}) == "code"
    assert contract.normalize_task_kind({"metadata": {"category": "Code"}}) == "code"


# -- caller-supplied lists must survive -----------------------------------

def test_caller_supplied_plan_steps_are_used_verbatim(contract):
    steps = ["Do exactly this.", "Then that."]
    built = contract.build_task_contract(
        {"task_kind": "general", "plan_steps": steps}
    )
    assert built["plan_steps"] == steps


def test_caller_supplied_validation_metrics_are_used_verbatim(contract):
    metrics = ["only_this"]
    built = contract.build_task_contract(
        {"task_kind": "code", "validation_metrics": metrics}
    )
    assert built["validation_metrics"] == metrics


def test_empty_caller_lists_do_not_override_the_defaults(contract):
    built = contract.build_task_contract(
        {"task_kind": "code", "plan_steps": [], "validation_metrics": ["  "]}
    )
    assert built["plan_steps"]
    assert built["validation_metrics"] == [
        "acceptance_criteria_met", "regressions_checked", "handoff_ready"
    ]


def test_caller_supplied_checkpoints_are_used(contract):
    built = contract.build_task_contract(
        {"task_kind": "general", "review_checkpoints": ["one"]}
    )
    assert built["review_checkpoints"] == ["one"]


# -- the rest of the contract shape ---------------------------------------

def test_contract_exposes_every_documented_key(contract):
    built = contract.build_task_contract({"task_kind": "code"})
    assert set(built) == {
        "task_kind", "requires_tools", "evidence_required", "capability_lane",
        "workflow_stage", "plan_steps", "validation_metrics", "review_checkpoints",
    }


def test_research_kinds_still_require_evidence(contract):
    assert contract.build_task_contract({"task_kind": "research"})["evidence_required"] is True


def test_code_kinds_do_not_force_evidence(contract):
    assert contract.build_task_contract({"task_kind": "code"})["evidence_required"] is False


def test_empty_payload_is_handled(contract):
    built = contract.build_task_contract({})
    assert built["task_kind"] == "general"
    assert built["requires_tools"] is False
    assert built["capability_lane"] == "prompt_only"


def test_non_dict_metadata_is_ignored(contract):
    built = contract.build_task_contract({"task": "fix the bug", "metadata": "nope"})
    assert built["task_kind"] == "code"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
