"""Read Harbor's own TB4 verifiers and summarize agent tool-loop diagnostics.

This module never substitutes self-reported task completion for official rewards.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


def inspect_trajectory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"trajectory_available": False}
    timeline = json.loads(path.read_text()).get("steps", [])
    counts: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    repeated = 0
    unsupported_images = False
    prior = None
    for step in timeline:
        if step.get("source") != "agent":
            continue
        for call in step.get("tool_calls") or []:
            name = call.get("function_name", "unknown")
            counts[name] += 1
            signature = (name, json.dumps(call.get("arguments") or {}, sort_keys=True))
            repeated += int(signature == prior)
            prior = signature
        for observation in (step.get("observation") or {}).get("results") or []:
            message = str(observation.get("content", ""))
            if (observation.get("extra") or {}).get("is_error"):
                errors[(observation.get("extra") or {}).get("tool_name", "unknown")] += 1
            if "current model does not support images" in message.lower():
                unsupported_images = True
    return {"trajectory_available": True, "agent_steps": sum(s.get("source") == "agent" for s in timeline),
            "tool_calls": dict(counts), "tool_errors": dict(errors),
            "consecutive_duplicate_calls": repeated,
            "reported_image_modality_unsupported": unsupported_images}



def inspect_pi_events(path: Path) -> dict[str, Any]:
    """Categorize model errors only; never copy prompts or errors into reports."""
    errors: Counter[str] = Counter()
    completed_turns = 0
    if not path.is_file():
        return {"pi_events_available": False}
    with path.open(errors="replace") as lines:
        for line in lines:
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if event.get("type") == "turn_end":
                completed_turns += 1
            if event.get("type") != "message_end":
                continue
            message = event.get("message") or {}
            if message.get("role") != "assistant" or message.get("stopReason") != "error":
                continue
            reason = str(message.get("errorMessage", "")).lower()
            if "this operation was aborted" in reason:
                category = "agent_turn_limit"
            elif "exceed_context_size_error" in reason or "exceeds the available context" in reason:
                category = "upstream_context_overflow"
            elif "invalid json payload" in reason or "immutable instructions" in reason:
                category = "gateway_rejected_request"
            elif "timed out" in reason or "timeout" in reason:
                category = "inference_timeout"
            else:
                category = "other_inference_error"
            errors[category] += 1
    return {"pi_events_available": True, "model_turns": completed_turns,
            "inference_errors": dict(errors)}


def report_job(job_dir: Path) -> dict[str, Any]:
    job = json.loads((job_dir / "result.json").read_text())
    trials: list[dict[str, Any]] = []
    for path in sorted(job_dir.glob("*/result.json")):
        result = json.loads(path.read_text())
        verifier = result.get("verifier_result") or {}
        reward = (verifier.get("rewards") or {}).get("reward")
        diagnosis = inspect_trajectory(path.parent / "agent" / "trajectory.json")
        diagnosis.update(inspect_pi_events(path.parent / "agent" / "pi.txt"))
        trials.append({"task": result.get("task_name"), "trial": result.get("trial_name"),
                       "reward": reward, "official_pass": reward == 1.0,
                       "exception": result.get("exception_info"),
                       "input_tokens": (result.get("agent_result") or {}).get("n_input_tokens"),
                       "output_tokens": (result.get("agent_result") or {}).get("n_output_tokens"),
                       "diagnostics": diagnosis})
    stats = job.get("stats") or {}
    # No inference from text responses, timestamps or process exit status.
    return {"job_id": job.get("id"), "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "official_completed": stats.get("n_completed_trials", 0),
            "official_errors": stats.get("n_errored_trials", 0),
            "official_evals": stats.get("evals", {}),
            "trial_count_with_results": len(trials),
            "passes_from_official_verifier": sum(x["official_pass"] for x in trials),
            "trials": trials}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Extract unmodified Harbor rewards and Pi diagnostics")
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    data = report_job(args.job_dir)
    output = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    print(output)


if __name__ == "__main__":
    main()
