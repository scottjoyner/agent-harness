"""Role contracts: SCOUT, IMPLEMENTER, REVIEWER.

Each role has a fixed structured output schema. The harness asks for a single
JSON object and nothing else. It never asks for hidden reasoning, chain of
thought, or private scratch space, and it never accepts prose in place of the
schema.

Role separation is the point of the swarm experiment: a scout localises and
explains, an implementer writes a candidate patch, and a reviewer adjudicates
against exact evidence. Each role is a single bounded call. There is no
unbounded agent conversation anywhere in this harness.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


class Role(str, Enum):
    SINGLE = "single"
    SCOUT = "scout"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class RoleProtocolError(ValueError):
    """Model output did not satisfy the role schema."""

    def __init__(self, message: str, outcome_hint: str = "PROTOCOL_FAILURE"):
        super().__init__(message)
        self.outcome_hint = outcome_hint


class ReviewVerdict(str, Enum):
    ACCEPT = "accept"
    REVISE = "revise"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


SCHEMAS: Dict[Role, Dict[str, Any]] = {
    Role.SCOUT: {
        "root_cause": "string, one or two sentences naming the mechanism of the defect",
        "relevant_files": "array of repo-relative paths that must change or be inspected",
        "plan": "array of short ordered implementation steps",
        "risks": "array of short risk statements",
        "confidence": "number between 0 and 1",
    },
    Role.IMPLEMENTER: {
        "patch": "string, a complete unified diff (\"diff --git\" headers, no commentary)",
        "tests": "array of short descriptions of tests to add or change",
        "assumptions": "array of short assumption statements",
        "confidence": "number between 0 and 1",
    },
    Role.REVIEWER: {
        "defects": "array of objects {severity, location, description}",
        "missing_coverage": "array of short descriptions of untested behaviour",
        "contract_violations": "array of short descriptions of violated constraints",
        "verdict": "string, exactly \"accept\" or \"revise\"",
        "confidence": "number between 0 and 1",
    },
}


def schema_block(role: Role) -> str:
    """Human/model readable schema description for ``role``."""
    fields = SCHEMAS[role]
    width = max(len(name) for name in fields)
    return "\n".join(
        "  {}  {}".format(name.ljust(width), spec) for name, spec in fields.items()
    )


# --------------------------------------------------------------------------
# Tolerant JSON extraction
# --------------------------------------------------------------------------

def extract_json_object(text: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Pull the first balanced JSON object out of ``text``.

    Returns ``(payload, truncated)``. ``payload`` is ``None`` when no balanced
    object exists. ``truncated`` is True when the text clearly ran out
    mid-structure (an opening brace with no match), which the caller maps to
    :data:`~realtask.taxonomy.Outcome.TRUNCATED` rather than a protocol error.
    """
    if not text:
        return None, False

    candidates: List[str] = []
    in_fence = False
    fence_lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                candidates.append("\n".join(fence_lines))
                fence_lines = []
                in_fence = False
            else:
                in_fence = True
            continue
        if in_fence:
            fence_lines.append(line)
    if fence_lines:
        candidates.append("\n".join(fence_lines))
    candidates.append(text)

    for candidate in candidates:
        payload, truncated = _first_balanced(candidate)
        if payload is not None:
            return payload, truncated
    _, truncated = _first_balanced(text)
    return None, truncated


def _first_balanced(text: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    start = text.find("{")
    if start < 0:
        return None, False
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None, False
                if isinstance(payload, dict):
                    return payload, False
                return None, False
    return None, True


# --------------------------------------------------------------------------
# Shared field coercion
# --------------------------------------------------------------------------

def _confine_str_list(value: Any, field_name: str, limit: int = 24) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items: Sequence[Any] = [line for line in value.splitlines() if line.strip()]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise RoleProtocolError("field {!r} must be a list of strings".format(field_name))
    out: List[str] = []
    for item in items:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = " ".join(
                str(v) for v in item.values() if isinstance(v, (str, int, float))
            ).strip()
        else:
            raise RoleProtocolError(
                "field {!r} entries must be strings or objects".format(field_name)
            )
        if text:
            out.append(text[:2000])
        if len(out) >= limit:
            break
    return tuple(out)


def _require_confidence(payload: Dict[str, Any]) -> float:
    if "confidence" not in payload:
        raise RoleProtocolError("missing required field 'confidence'")
    raw = payload["confidence"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise RoleProtocolError("field 'confidence' must be a number between 0 and 1")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RoleProtocolError("field 'confidence' must be numeric") from exc
    if not 0.0 <= value <= 1.0:
        raise RoleProtocolError("field 'confidence' must be between 0 and 1")
    return value


def _require_key(payload: Dict[str, Any], name: str) -> Any:
    if name not in payload:
        raise RoleProtocolError("missing required field {!r}".format(name))
    return payload[name]


# --------------------------------------------------------------------------
# Role results
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoutResult:
    root_cause: str
    relevant_files: Tuple[str, ...]
    plan: Tuple[str, ...]
    risks: Tuple[str, ...]
    confidence: float

    role = Role.SCOUT

    @classmethod
    def parse(cls, text: str) -> "ScoutResult":
        payload, truncated = extract_json_object(text)
        if payload is None:
            raise RoleProtocolError(
                "scout output contained no JSON object", "TRUNCATED" if truncated else "PROTOCOL_FAILURE"
            )
        try:
            return cls(
                root_cause=str(_require_key(payload, "root_cause")).strip(),
                relevant_files=_confine_str_list(payload.get("relevant_files"), "relevant_files"),
                plan=_confine_str_list(payload.get("plan"), "plan"),
                risks=_confine_str_list(payload.get("risks"), "risks"),
                confidence=_require_confidence(payload),
            )
        except RoleProtocolError as exc:
            if truncated:
                raise RoleProtocolError(str(exc), "TRUNCATED") from exc
            raise

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role.value,
            "root_cause": self.root_cause,
            "relevant_files": list(self.relevant_files),
            "plan": list(self.plan),
            "risks": list(self.risks),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ImplementerResult:
    patch: str
    tests: Tuple[str, ...]
    assumptions: Tuple[str, ...]
    confidence: float

    role = Role.IMPLEMENTER

    @classmethod
    def parse(cls, text: str) -> "ImplementerResult":
        payload, truncated = extract_json_object(text)
        if payload is None:
            raise RoleProtocolError(
                "implementer output contained no JSON object",
                "TRUNCATED" if truncated else "PROTOCOL_FAILURE",
            )
        try:
            patch = _require_key(payload, "patch")
            if not isinstance(patch, str):
                raise RoleProtocolError("field 'patch' must be a string")
            return cls(
                patch=patch,
                tests=_confine_str_list(payload.get("tests"), "tests"),
                assumptions=_confine_str_list(payload.get("assumptions"), "assumptions"),
                confidence=_require_confidence(payload),
            )
        except RoleProtocolError as exc:
            if truncated:
                raise RoleProtocolError(str(exc), "TRUNCATED") from exc
            raise

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role.value,
            "patch": self.patch,
            "tests": list(self.tests),
            "assumptions": list(self.assumptions),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ReviewDefect:
    severity: str
    location: str
    description: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "location": self.location,
            "description": self.description,
        }


@dataclass(frozen=True)
class ReviewerResult:
    defects: Tuple[ReviewDefect, ...]
    missing_coverage: Tuple[str, ...]
    contract_violations: Tuple[str, ...]
    verdict: ReviewVerdict
    confidence: float

    role = Role.REVIEWER

    @classmethod
    def parse(cls, text: str) -> "ReviewerResult":
        payload, truncated = extract_json_object(text)
        if payload is None:
            raise RoleProtocolError(
                "reviewer output contained no JSON object",
                "TRUNCATED" if truncated else "PROTOCOL_FAILURE",
            )
        try:
            verdict_raw = _require_key(payload, "verdict")
            verdict_text = str(verdict_raw).strip().lower()
            if verdict_text not in ("accept", "revise"):
                raise RoleProtocolError(
                    "field 'verdict' must be exactly 'accept' or 'revise'"
                )
            return cls(
                defects=_confine_defects(payload.get("defects")),
                missing_coverage=_confine_str_list(
                    payload.get("missing_coverage"), "missing_coverage"
                ),
                contract_violations=_confine_str_list(
                    payload.get("contract_violations"), "contract_violations"
                ),
                verdict=ReviewVerdict(verdict_text),
                confidence=_require_confidence(payload),
            )
        except RoleProtocolError as exc:
            if truncated:
                raise RoleProtocolError(str(exc), "TRUNCATED") from exc
            raise

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role.value,
            "defects": [d.to_dict() for d in self.defects],
            "missing_coverage": list(self.missing_coverage),
            "contract_violations": list(self.contract_violations),
            "verdict": self.verdict.value,
            "confidence": self.confidence,
        }


def _confine_defects(value: Any) -> Tuple[ReviewDefect, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise RoleProtocolError("field 'defects' must be a list of objects")
    out: List[ReviewDefect] = []
    for entry in value:
        if isinstance(entry, dict):
            out.append(
                ReviewDefect(
                    severity=str(entry.get("severity", "unspecified"))[:64],
                    location=str(entry.get("location", ""))[:512],
                    description=str(entry.get("description", ""))[:2000],
                )
            )
        elif isinstance(entry, str):
            out.append(ReviewDefect(severity="unspecified", location="", description=entry[:2000]))
        else:
            raise RoleProtocolError("field 'defects' entries must be objects or strings")
        if len(out) >= 32:
            break
    return tuple(out)


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------

_CONTRACT_PREAMBLE = (
    "You are one role in a bounded benchmark. You have NO tools, NO shell and "
    "NO filesystem access. You cannot read or write anything except the source "
    "text included in this prompt. Reply with exactly one JSON object and no "
    "other text. Do not include commentary, markdown fences, or reasoning "
    "outside the object. Do not ask questions."
)


#: Envelopes that mean "I am calling a tool" rather than "here is my answer".
#: The harness offers no tools -- the bound source is in the prompt -- and every
#: role contract says so, so these are a model addressing an interface that does
#: not exist here. They are recognised so the evidence can say that, instead of
#: filing a tool call under whichever outcome the truncation happened to produce.
_TOOL_CALL_PATTERNS = (
    re.compile(r"<\s*tool_call\b", re.IGNORECASE),
    re.compile(r"<\s*\|?\s*(?:tool_call|function_call)\s*\|?\s*>", re.IGNORECASE),
    re.compile(r"<tool_call>", re.IGNORECASE),
)

_TOOL_NAME = re.compile(r"""name\s*=\s*["']([A-Za-z_][\w.\-]{0,64})["']""")


def requested_tools(text: str) -> Tuple[str, ...]:
    """Names of tools the reply asked to call, in order, without duplicates."""
    found: List[str] = []
    for pattern in _TOOL_CALL_PATTERNS:
        for match in pattern.finditer(text or ""):
            tail = text[match.start(): match.start() + 400]
            name = _TOOL_NAME.search(tail)
            found.append(name.group(1) if name else "(unnamed)")
    out: List[str] = []
    for name in found:
        if name not in out:
            out.append(name)
    return tuple(out)


def _json_only_instruction(role: Role) -> str:
    return (
        "Return a single JSON object with exactly these keys:\n{}\n"
        "All string values must be JSON strings. The 'patch' value must contain "
        "the literal unified diff text including newlines (escape them as \\n)."
    ).format(schema_block(role))


def source_block(paths: Sequence[str], contents: Dict[str, str], truncated: Sequence[str]) -> str:
    parts: List[str] = ["SOURCE FILES (verbatim, read-only):"]
    for path in paths:
        if path in truncated:
            parts.append("--- BEGIN {} (TRUNCATED) ---".format(path))
            continue
        parts.append("--- BEGIN {} ---".format(path))
        parts.append(contents.get(path, ""))
        parts.append("--- END {} ---".format(path))
    return "\n".join(parts)


def task_block(task_problem: str, constraints: Sequence[str], acceptance_lines: Sequence[str]) -> str:
    parts = ["TASK:", task_problem.strip()]
    if constraints:
        parts.append("")
        parts.append("CONSTRAINTS:")
        parts.extend("- {}".format(c) for c in constraints)
    if acceptance_lines:
        parts.append("")
        parts.append("ACCEPTANCE (the harness runs these against your patch):")
        parts.extend("- {}".format(line) for line in acceptance_lines)
    return "\n".join(parts)


def scout_prompt(problem: str, constraints: Sequence[str], acceptance_lines: Sequence[str],
                 sources: str, expected_files: Sequence[str]) -> str:
    parts = [
        "ROLE: SCOUT",
        _CONTRACT_PREAMBLE,
        "Locate and explain the defect. Name the exact files that must change. "
        "Produce a short ordered plan. Do not write code.",
        "",
        _json_only_instruction(Role.SCOUT),
        "",
        "relevant_files must use these exact repo-relative paths where applicable:",
        ", ".join(expected_files) if expected_files else "(any relevant path)",
        "",
        task_block(problem, constraints, acceptance_lines),
        "",
        sources,
    ]
    return "\n".join(parts)


def implementer_prompt(problem: str, constraints: Sequence[str], acceptance_lines: Sequence[str],
                       sources: str, scout: Optional[ScoutResult] = None) -> str:
    parts = [
        "ROLE: IMPLEMENTER",
        _CONTRACT_PREAMBLE,
        "Produce a candidate unified diff against the source files below. The "
        "diff is applied by the harness inside a disposable copy of the source; "
        "it is never applied to the authoritative repository. Emit the complete "
        "diff including file headers. Touch only files you must change.",
        "",
        _json_only_instruction(Role.IMPLEMENTER),
    ]
    if scout is not None:
        parts.extend(
            [
                "",
                "SCOUT REPORT (from a prior role; treat as unverified input):",
                "  root_cause: {}".format(scout.root_cause),
                "  relevant_files: {}".format(", ".join(scout.relevant_files) or "(none)"),
                "  plan:",
            ]
        )
        parts.extend("    {}. {}".format(i + 1, step) for i, step in enumerate(scout.plan))
        if scout.risks:
            parts.append("  risks: {}".format("; ".join(scout.risks)))
        parts.append("  confidence: {}".format(scout.confidence))
    parts.extend(["", task_block(problem, constraints, acceptance_lines), "", sources])
    return "\n".join(parts)


def refinement_prompt(problem: str, constraints: Sequence[str], acceptance_lines: Sequence[str],
                      sources: str, scout: Optional[ScoutResult],
                      review_text: str, reviewer: ReviewerResult,
                      patch_text: str) -> str:
    parts = [
        "ROLE: IMPLEMENTER (single revision round)",
        _CONTRACT_PREAMBLE,
        "A reviewer adjudicated your previous candidate and asked for changes. "
        "This is the ONLY revision round permitted. Emit a single complete "
        "unified diff that addresses the review. Do not restate the old diff.",
        "",
        _json_only_instruction(Role.IMPLEMENTER),
        "",
        "REVIEWER VERDICT: {}".format(reviewer.verdict.value),
        "",
        "REVIEWER FINDINGS (verbatim structured output):",
        reviewer.to_json_text(),
        "",
        "REVIEWER RAW OUTPUT:",
        review_text.strip()[:20000],
        "",
        "PREVIOUS CANDIDATE PATCH (verbatim; this is what the reviewer saw):",
        patch_text.strip()[:60000] or "(empty)",
        "",
        task_block(problem, constraints, acceptance_lines),
        "",
        sources,
    ]
    if scout is not None:
        parts.extend(
            [
                "",
                "ORIGINAL SCOUT REPORT:",
                "  root_cause: {}".format(scout.root_cause),
                "  relevant_files: {}".format(", ".join(scout.relevant_files) or "(none)"),
            ]
        )
    return "\n".join(parts)


def reviewer_prompt(problem: str, constraints: Sequence[str],
                    binding_block: str, candidate_text: str,
                    test_evidence: str, candidate_label: str = "CANDIDATE PATCH") -> str:
    parts = [
        "ROLE: REVIEWER",
        _CONTRACT_PREAMBLE,
        "Adjudicate the exact candidate below. You are given the exact source "
        "binding and the exact test evidence. Judge only what is shown. "
        "Do not speculate about files you cannot see in the binding.",
        "",
        _json_only_instruction(Role.REVIEWER),
        "",
        binding_block,
        "",
        task_block(problem, constraints, ()),
        "",
        "{} (verbatim; this is the exact text the harness received)".format(candidate_label),
        candidate_text.strip()[:60000] or "(empty)",
        "",
        "TEST EVIDENCE (verbatim harness output):",
        test_evidence.strip()[:40000],
    ]
    return "\n".join(parts)


def single_analysis_prompt(problem: str, constraints: Sequence[str],
                           acceptance_lines: Sequence[str], sources: str,
                           task_id: str, task_family: str) -> str:
    parts = [
        "ROLE: SINGLE (one model, full bounded task context, no role separation)",
        _CONTRACT_PREAMBLE,
        "This task is analysis-only. Produce no patch and no diff. Identify the "
        "root cause, name the exact files involved, and give a short ordered "
        "plan of what a correct repair would require.",
        "",
        _json_only_instruction(Role.SCOUT),
        "",
        "TASK_ID: {}".format(task_id),
        "TASK_FAMILY: {}".format(task_family),
        "",
        task_block(problem, constraints, acceptance_lines),
        "",
        sources,
    ]
    return "\n".join(parts)


def single_prompt(problem: str, constraints: Sequence[str], acceptance_lines: Sequence[str],
                  sources: str, task_id: str, task_family: str) -> str:
    parts = [
        "ROLE: SINGLE (one model, full bounded task context, no role separation)",
        _CONTRACT_PREAMBLE,
        "You receive the whole task at once: investigate it and produce the fix "
        "in a single reply. The 'patch' value must be a complete unified diff "
        "against the source files below. Touch only files you must change.",
        "",
        _json_only_instruction(Role.IMPLEMENTER),
        "",
        "TASK_ID: {}".format(task_id),
        "TASK_FAMILY: {}".format(task_family),
        "",
        task_block(problem, constraints, acceptance_lines),
        "",
        sources,
    ]
    return "\n".join(parts)


def scout_result_from_evidence(payload: Dict[str, Any]) -> ScoutResult:
    """Rebuild a :class:`ScoutResult` from a recorded ``scout`` evidence block.

    Lets a scout run be inspected in one invocation and handed to an
    implementer in the next, without re-asking the model or hand-transcribing the
    result. Fails closed on anything that is not a well-formed scout block.
    """
    if not isinstance(payload, dict):
        raise ValueError("scout evidence block must be an object")
    missing = [f for f in ("root_cause", "relevant_files", "plan", "risks", "confidence")
               if f not in payload]
    if missing:
        raise ValueError(
            "scout evidence block is missing {}".format(", ".join(missing))
        )
    return ScoutResult(
        root_cause=str(payload["root_cause"]),
        relevant_files=tuple(str(x) for x in payload["relevant_files"]),
        plan=tuple(str(x) for x in payload["plan"]),
        risks=tuple(str(x) for x in payload["risks"]),
        confidence=float(payload["confidence"]),
    )


def _to_json_text(self: ReviewerResult) -> str:
    return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


ReviewerResult.to_json_text = _to_json_text  # type: ignore[attr-defined]
