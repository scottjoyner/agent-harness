from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
from typing import Any

DEFAULT_MANIFEST = pathlib.Path("benchmarks/system_one/browser_decisions.json")
AUTHORITY = {
    "dispatch_allowed": False,
    "approval_granted": False,
    "claim_acquired": False,
    "mutation_allowed": False,
    "routing_authority_changed": False,
}


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _validate_question(name: str, question: dict[str, Any]) -> None:
    if question.get("type") not in {"choice", "noul", "score"}:
        raise ValueError(f"{name}: invalid question type")
    if not str(question.get("instructions", "")).strip():
        raise ValueError(f"{name}: instructions are required")
    options = question.get("options")
    if not isinstance(options, list) or len(options) < 2:
        raise ValueError(f"{name}: at least two options are required")
    if len(options) != len(set(options)):
        raise ValueError(f"{name}: options must be unique")
    if question["type"] == "noul" and options != ["false", "true"]:
        raise ValueError(f"{name}: noul options must be false/true")


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != "system-one-browser-fixtures-v1":
        raise ValueError("unexpected browser fixture schema")
    semantics = manifest.get("semantics")
    if not isinstance(semantics, dict):
        raise ValueError("semantics are required")
    required = {
        "non_destructive": True,
        "browser_execution": False,
        "network_access": False,
        "tools_are_simulated": True,
        "page_text_is_untrusted_data": True,
        "authority_changes_allowed": False,
    }
    for key, expected in required.items():
        if semantics.get(key) is not expected:
            raise ValueError(f"unsafe or missing semantic: {key}")

    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases are required")
    ids: set[str] = set()
    for case in cases:
        case_id = str(case.get("id", ""))
        if not case_id or case_id in ids:
            raise ValueError("case IDs must be present and unique")
        ids.add(case_id)
        if case.get("family") != "browser_decision":
            raise ValueError(f"{case_id}: unexpected family")
        if not isinstance(case.get("state"), str) or not case["state"]:
            raise ValueError(f"{case_id}: state must be a non-empty string")
        questions = case.get("questions")
        if not isinstance(questions, dict) or not questions:
            raise ValueError(f"{case_id}: questions are required")
        for name, question in questions.items():
            if not isinstance(question, dict):
                raise ValueError(f"{case_id}/{name}: question must be an object")
            _validate_question(name, question)
        expected = case.get("expected", {}).get("choices", {})
        if not isinstance(expected, dict) or "operation" not in expected:
            raise ValueError(f"{case_id}: expected operation is required")
        for name, choice in expected.items():
            if name not in questions:
                raise ValueError(f"{case_id}: expected choice references unknown question {name}")
            if choice not in questions[name]["options"]:
                raise ValueError(f"{case_id}/{name}: expected choice is not legal")


def hashes_for_case(case: dict[str, Any]) -> dict[str, str]:
    question_schema = {
        name: {
            "type": question["type"],
            "instructions": question["instructions"],
            "options": list(question["options"]),
        }
        for name, question in case["questions"].items()
    }
    candidate_set = {
        name: list(question["options"])
        for name, question in case["questions"].items()
    }
    return {
        "input_sha256": sha256_json({"state": case["state"]}),
        "question_schema_sha256": sha256_json(question_schema),
        "candidate_set_sha256": sha256_json(candidate_set),
    }


def _hex64(value: Any, field: str) -> str:
    text = str(value)
    if len(text) != 64:
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    try:
        int(text, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a SHA-256 hex digest") from exc
    return text


def validate_receipt(case: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    if receipt.get("schema") != "system-one-decision-receipt-v1":
        raise ValueError("unexpected decision receipt schema")
    if receipt.get("evidence_only") is not True:
        raise ValueError("receipt must remain evidence-only")
    if receipt.get("authority") != AUTHORITY:
        raise ValueError("receipt authority must remain all-false")

    expected_hashes = hashes_for_case(case)
    for field, expected in expected_hashes.items():
        if _hex64(receipt.get(field), field) != expected:
            raise ValueError(f"{field} does not match the frozen fixture")
    _hex64(receipt.get("response_sha256"), "response_sha256")

    provider = receipt.get("provider")
    if not isinstance(provider, dict):
        raise ValueError("provider evidence is required")
    for field in ("provider_id", "provider_version", "model_id"):
        if not str(provider.get(field, "")).strip():
            raise ValueError(f"provider.{field} is required")
    artifact = provider.get("model_artifact_sha256")
    if artifact is not None:
        _hex64(artifact, "provider.model_artifact_sha256")

    latency = receipt.get("latency_ms")
    if not isinstance(latency, (int, float)) or not math.isfinite(float(latency)) or latency < 0:
        raise ValueError("latency_ms must be finite and non-negative")

    results = receipt.get("questions")
    if not isinstance(results, list) or not results:
        raise ValueError("receipt questions are required")
    if sha256_json(results) != receipt["response_sha256"]:
        raise ValueError("response_sha256 does not bind the question results")

    by_name: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("question result must be an object")
        name = str(result.get("name", ""))
        if not name or name in by_name:
            raise ValueError("question result names must be present and unique")
        by_name[name] = result

    if set(by_name) != set(case["questions"]):
        raise ValueError("receipt question set does not match the fixture")

    expected_choices = case["expected"]["choices"]
    for name, question in case["questions"].items():
        result = by_name[name]
        if result.get("type") != question["type"]:
            raise ValueError(f"{name}: result type mismatch")
        if result.get("options") != question["options"]:
            raise ValueError(f"{name}: result options mismatch")
        probabilities = result.get("probabilities")
        if not isinstance(probabilities, list) or len(probabilities) != len(question["options"]):
            raise ValueError(f"{name}: invalid probability vector")
        if any(
            not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
            or value > 1
            for value in probabilities
        ):
            raise ValueError(f"{name}: probabilities must be finite values in [0,1]")
        if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-5):
            raise ValueError(f"{name}: probabilities must sum to 1")
        choice = result.get("choice")
        if choice not in question["options"]:
            raise ValueError(f"{name}: choice is not a legal option")
        selected = float(probabilities[question["options"].index(choice)])
        if not math.isclose(selected, max(probabilities), rel_tol=0.0, abs_tol=1e-6):
            raise ValueError(f"{name}: choice is not a maximum-probability option")
        confidence = result.get("confidence")
        if confidence is not None and not math.isclose(
            float(confidence), selected, rel_tol=0.0, abs_tol=1e-5
        ):
            raise ValueError(f"{name}: confidence does not match selected probability")
        if name in expected_choices and choice != expected_choices[name]:
            raise ValueError(
                f"{name}: expected {expected_choices[name]!r}, received {choice!r}"
            )

    return {
        "schema_version": "system-one-browser-receipt-validation-v1",
        "case_id": case["id"],
        "provider_id": provider["provider_id"],
        "model_id": provider["model_id"],
        "passed": True,
        "response_sha256": receipt["response_sha256"],
        "authority_unchanged": True,
    }


def case_by_id(manifest: dict[str, Any], case_id: str) -> dict[str, Any]:
    for case in manifest["cases"]:
        if case["id"] == case_id:
            return case
    raise ValueError(f"unknown case: {case_id}")


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_json(args.manifest)
    validate_manifest(manifest)
    if args.validate_only:
        return {
            "schema_version": "system-one-browser-fixture-validation-v1",
            "suite_revision": manifest["suite_revision"],
            "manifest_sha256": sha256_json(manifest),
            "case_count": len(manifest["cases"]),
            "browser_execution": False,
            "network_access": False,
            "authority_changes_allowed": False,
        }
    if not args.case or not args.receipt:
        raise ValueError("--case and --receipt are required unless --validate-only is used")
    case = case_by_id(manifest, args.case)
    receipt = load_json(args.receipt)
    return validate_receipt(case, receipt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--case")
    parser.add_argument("--receipt", type=pathlib.Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    result = run(args)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
