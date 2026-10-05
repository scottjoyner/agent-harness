from __future__ import annotations

from typing import Any


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _string(value: Any) -> str:
    return str(value or "").strip()


def normalize_task_kind(payload: dict[str, Any]) -> str:
    metadata = _metadata(payload)
    explicit = _string(payload.get("task_kind") or metadata.get("task_kind") or metadata.get("kind") or metadata.get("category"))
    if explicit:
        kind = explicit.lower().replace(" ", "_")
        return {"coding": "code"}.get(kind, kind)

    text = " ".join(
        _string(payload.get(key) or metadata.get(key)).lower()
        for key in ("task", "prompt", "summary", "description", "context", "notes", "title")
    )
    keyword_map = [
        ("research", ("research", "investigate", "evidence", "source", "cite", "browse", "lookup")),
        ("analysis", ("analy", "measure", "metrics", "perf", "performance", "compare", "review")),
        ("refinement", ("refine", "rewrite", "polish", "edit", "summarize", "summary")),
        ("operations", ("deploy", "service", "ops", "monitor", "health", "diagnostic")),
        ("code", ("code", "implement", "patch", "bug", "test", "refactor")),
    ]
    for kind, needles in keyword_map:
        if any(needle in text for needle in needles):
            return kind
    return "general"


def task_requires_tools(payload: dict[str, Any]) -> bool:
    metadata = _metadata(payload)
    if "requires_tools" in payload:
        return bool(payload.get("requires_tools"))
    if "requires_tools" in metadata:
        return bool(metadata.get("requires_tools"))
    if bool(payload.get("finalized")) or bool(payload.get("reviewed")) or bool(metadata.get("finalized")) or bool(metadata.get("reviewed")):
        return True
    workflow_stage = _string(payload.get("workflow_stage") or payload.get("stage") or metadata.get("workflow_stage") or metadata.get("stage")).lower()
    if workflow_stage in {"handoff", "final", "finalized", "review_final"}:
        return True
    return normalize_task_kind(payload) in {"research", "analysis", "operations", "code"}


def task_evidence_required(payload: dict[str, Any]) -> bool:
    metadata = _metadata(payload)
    if "evidence_required" in payload:
        return bool(payload.get("evidence_required"))
    if "evidence_required" in metadata:
        return bool(metadata.get("evidence_required"))
    return normalize_task_kind(payload) in {"research", "analysis"}


def task_capability_lane(payload: dict[str, Any]) -> str:
    metadata = _metadata(payload)
    explicit = _string(payload.get("capability_lane") or metadata.get("capability_lane") or metadata.get("lane"))
    if explicit:
        return explicit.lower()
    return "tool_required" if task_requires_tools(payload) else "prompt_only"


def task_workflow_stage(payload: dict[str, Any]) -> str:
    metadata = _metadata(payload)
    explicit = _string(payload.get("workflow_stage") or payload.get("stage") or metadata.get("workflow_stage") or metadata.get("stage"))
    if explicit:
        return explicit.lower()
    if bool(payload.get("finalized")) or bool(payload.get("reviewed")) or bool(metadata.get("finalized")) or bool(metadata.get("reviewed")):
        return "handoff"
    if task_requires_tools(payload):
        return "iterative"
    return "prompt_only"


def task_plan_steps(payload: dict[str, Any]) -> list[str]:
    explicit = payload.get("plan_steps")
    if isinstance(explicit, list) and any(_string(item) for item in explicit):
        return [_string(item) for item in explicit if _string(item)]

    task_kind = normalize_task_kind(payload)
    if task_kind in {"code", "implementation", "refinement", "repair", "review", "repo", "patch"}:
        return [
            "Inspect the current state one slice at a time.",
            "Make the smallest safe change or conclusion.",
            "Validate the result against the acceptance criteria.",
            "Report risks, gaps, and handoff notes.",
        ]
    if task_kind in {"research", "analysis", "documentation", "docs"}:
        return [
            "Gather the relevant evidence or context.",
            "Compare the options and identify the best path.",
            "Draft the answer or recommendation.",
            "Verify claims, cite sources, and finalize the handoff.",
        ]
    if task_kind in {"operations", "terminal", "shell"}:
        return [
            "Inspect the live state.",
            "Apply the smallest safe operation.",
            "Verify service health or output.",
            "Record what changed and what remains.",
        ]
    return [
        "Clarify the immediate goal.",
        "Advance the task in one small step.",
        "Validate the result.",
        "Summarize the next handoff.",
    ]


def task_validation_metrics(payload: dict[str, Any]) -> list[str]:
    explicit = payload.get("validation_metrics")
    if isinstance(explicit, list) and any(_string(item) for item in explicit):
        return [_string(item) for item in explicit if _string(item)]

    task_kind = normalize_task_kind(payload)
    if task_kind in {"code", "implementation", "refinement", "repair", "review", "repo", "patch"}:
        return ["acceptance_criteria_met", "regressions_checked", "handoff_ready"]
    if task_kind in {"research", "analysis", "documentation", "docs"}:
        return ["evidence_captured", "claims_supported", "final_answer_ready"]
    if task_kind in {"operations", "terminal", "shell"}:
        return ["state_verified", "change_applied", "health_confirmed"]
    return ["goal_understood", "next_step_defined", "handoff_ready"]


def task_review_checkpoints(payload: dict[str, Any]) -> list[str]:
    explicit = payload.get("review_checkpoints")
    if isinstance(explicit, list) and any(_string(item) for item in explicit):
        return [_string(item) for item in explicit if _string(item)]
    return ["reviewed by local iteration", "validated against plan", "final handoff approved"]


def build_task_contract(payload: dict[str, Any]) -> dict[str, Any]:
    contract = {
        "task_kind": normalize_task_kind(payload),
        "requires_tools": task_requires_tools(payload),
        "evidence_required": task_evidence_required(payload),
        "capability_lane": task_capability_lane(payload),
        "workflow_stage": task_workflow_stage(payload),
        "plan_steps": task_plan_steps(payload),
        "validation_metrics": task_validation_metrics(payload),
        "review_checkpoints": task_review_checkpoints(payload),
    }
    return contract
