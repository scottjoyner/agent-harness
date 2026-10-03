"""Candidate patch handling: extraction, safety screening, application.

Models never edit the authoritative source. They return patch *text*; this
module decides whether that text is well-formed, whether it is safe to apply,
and applies it only inside a disposable evaluation worktree owned by the
harness.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

_FENCE_RE = re.compile(r"```(?:diff|patch|unified)?[ \t]*\r?\n(.*?)```", re.DOTALL)
_DIFF_HEADER_RE = re.compile(r"^diff --git ", re.MULTILINE)
_PLUS_RE = re.compile(r"^\+\+\+ (?:b/)?(.+?)\t?$", re.MULTILINE)


@dataclass
class ExtractedPatch:
    text: str
    strategy: str

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def normalize_patch(text: str) -> str:
    """Ensure a unified diff is newline-terminated exactly once.

    ``git apply`` and ``patch`` both reject a final hunk line that has no
    trailing newline as a corrupt patch, and a diff round-tripped through a JSON
    string field routinely loses it. Normalising here keeps that from being
    reported as INVALID_PATCH when the candidate's diff is in fact well formed.
    """
    stripped = text.rstrip("\n")
    return stripped + "\n" if stripped else ""


def extract_patch(raw: str) -> ExtractedPatch:
    """Recover a unified diff from free-form model output.

    Strategies, in order: an explicit fenced block, the region starting at the
    first ``diff --git`` header, then the raw text if it already looks like a
    diff. The strategy actually used is recorded in the evidence.
    """
    if not raw or not raw.strip():
        return ExtractedPatch("", "empty")

    for match in _FENCE_RE.finditer(raw):
        body = match.group(1)
        if "--- " in body and "+++ " in body:
            return ExtractedPatch(normalize_patch(body), "fenced_diff")

    if _DIFF_HEADER_RE.search(raw):
        start = raw.index("diff --git ")
        return ExtractedPatch(normalize_patch(raw[start:]), "diff_git_region")

    if re.search(r"^--- ", raw, re.MULTILINE) and re.search(r"^\+\+\+ ", raw, re.MULTILINE):
        return ExtractedPatch(normalize_patch(raw), "bare_unified_diff")

    return ExtractedPatch("", "none")


@dataclass
class PatchSafetyReport:
    ok: bool
    reason: Optional[str] = None
    paths: Tuple[str, ...] = ()
    outside_worktree: Tuple[str, ...] = ()
    unknown_paths: Tuple[str, ...] = ()
    binary: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "paths": list(self.paths),
            "outside_worktree": list(self.outside_worktree),
            "unknown_paths": list(self.unknown_paths),
            "binary": self.binary,
        }


def parse_patch_paths(patch_text: str) -> Set[str]:
    """Repo-relative paths touched by ``patch_text``."""
    paths: Set[str] = set()
    for match in _PLUS_RE.finditer(patch_text):
        raw = match.group(1).strip()
        if raw == "/dev/null":
            continue
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        if raw:
            paths.add(raw)
    return paths


def screen_patch(
    patch_text: str,
    known_paths: Iterable[str],
    writable_prefixes: Iterable[str] = (),
) -> PatchSafetyReport:
    """Reject patches that escape the worktree or touch undeclared files.

    ``known_paths`` is the frozen set of files the fixture binds. A patch that
    creates or modifies anything else is refused, unless it lands under one of
    ``writable_prefixes`` -- the fixture's own bounded scratch area, used for
    example by ``test_generation`` tasks where the candidate must add a new
    test file. Prefixes are always relative and never escape the worktree.
    """
    known = set(known_paths)
    prefixes = tuple(writable_prefixes)
    if "GIT binary patch" in patch_text or "Binary files " in patch_text:
        return PatchSafetyReport(ok=False, reason="binary patch is not supported", binary=True)
    if "rename from" in patch_text or "rename to" in patch_text:
        return PatchSafetyReport(ok=False, reason="renames are not supported")

    paths = parse_patch_paths(patch_text)
    if not paths:
        return PatchSafetyReport(ok=False, reason="patch declares no file paths")

    outside: List[str] = []
    for path in sorted(paths):
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            outside.append(path)
    if outside:
        return PatchSafetyReport(
            ok=False,
            reason="patch touches paths outside the evaluation worktree",
            paths=tuple(sorted(paths)),
            outside_worktree=tuple(outside),
        )

    unknown = sorted(
        p
        for p in paths
        if p not in known and not any(p.startswith(prefix) for prefix in prefixes)
    )
    if unknown:
        return PatchSafetyReport(
            ok=False,
            reason="patch touches files not declared by the fixture source binding",
            paths=tuple(sorted(paths)),
            unknown_paths=tuple(unknown),
        )

    return PatchSafetyReport(ok=True, paths=tuple(sorted(paths)))


@dataclass
class ApplyResult:
    ok: bool
    outcome_hint: Optional[str] = None
    reason: str = ""
    applier: str = ""
    safety_ok: bool = True
    safety_reason: str = ""
    stdout: str = ""
    stderr: str = ""
    before_hashes: Dict[str, str] = field(default_factory=dict)
    after_hashes: Dict[str, str] = field(default_factory=dict)

    @property
    def changed_files(self) -> Tuple[str, ...]:
        return tuple(
            sorted(
                path
                for path in set(self.before_hashes) | set(self.after_hashes)
                if self.before_hashes.get(path) != self.after_hashes.get(path)
            )
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "outcome_hint": self.outcome_hint,
            "reason": self.reason,
            "applier": self.applier,
            "safety_ok": self.safety_ok,
            "safety_reason": self.safety_reason,
            "stdout": self.stdout[:8000],
            "stderr": self.stderr[:8000],
            "changed_files": list(self.changed_files),
        }


def hash_tree(root: Path, paths: Sequence[str]) -> Dict[str, str]:
    """SHA-256 of each existing path under ``root``; absent paths map to ``""``."""
    import hashlib

    out: Dict[str, str] = {}
    for rel in sorted(set(paths)):
        target = root / rel
        if not target.is_file():
            out[rel] = ""
            continue
        digest = hashlib.sha256()
        with open(target, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        out[rel] = digest.hexdigest()
    return out


def syntax_check(root: Path, paths: Iterable[str]) -> Tuple[bool, Dict[str, str]]:
    """Compile every changed ``.py`` file in place. No subprocess, no mutation."""
    errors: Dict[str, str] = {}
    for rel in sorted(set(paths)):
        if not rel.endswith(".py"):
            continue
        target = root / rel
        if not target.is_file():
            errors[rel] = "file missing after patch"
            continue
        source = target.read_text(encoding="utf-8", errors="replace")
        try:
            compile(source, str(rel), "exec")
        except SyntaxError as exc:
            errors[rel] = "line {}: {}".format(exc.lineno, exc.msg)
    return (not errors), errors
