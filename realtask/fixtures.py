"""Frozen real-task fixture loading and validation.

A :class:`RealTask` is a self-describing, reproducible unit of benchmark work.
It never depends on a mutable checkout: the relevant source files are carried
inside the fixture directory under ``source/`` and are bound by SHA-256.

Directory layout of a fixture::

    realtask/tasks/<task_id>/
        task.json                 # the frozen manifest (this module's input)
        source-manifest.json      # authoritative per-file hash manifest
        source/                   # bounded source snapshot, repo-relative paths
        tests/                    # bounded acceptance tests, run in a copy
        README.md                 # optional human note

Validation is *strict*: unknown keys, unknown enum members, and unresolvable
paths all raise. A fixture that cannot be fully understood is never benchmarked.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .taxonomy import Outcome
from .version import SCHEMA_SOURCE_MANIFEST, SCHEMA_TASK, TASK_SCHEMA_VERSION

#: Task families the harness understands. The corpus should come from work we
#: actually need; synthetic coding puzzles are explicitly not the primary
#: corpus, so no family here is a puzzle family.
TASK_FAMILIES = (
    "bug_fix",
    "test_generation",
    "code_review",
    "small_refactor",
    "contract_reasoning",
)

#: What the model is expected to produce.
DELIVERABLES = ("patch", "analysis")

_TASK_KEYS = {
    "schema", "schema_version", "task_id", "task_family", "fixture_version",
    "title", "deliverable", "problem", "constraints", "relevant_source_files",
    "prompt_source_files", "known_regression", "source", "acceptance",
}
_SOURCE_KEYS = {"repository", "head", "manifest_sha256", "files", "writable_prefixes"}
_FILE_KEYS = {"path", "sha256", "size_bytes"}
_ACCEPTANCE_KEYS = {
    "targeted", "broader", "allow_programs", "required_answer_contains",
    "required_files_mentioned",
}


class FixtureError(ValueError):
    """Raised when a fixture directory or manifest is unusable.

    Carries an :class:`Outcome` so callers can fail closed with the correct
    taxonomy entry rather than a generic crash.
    """

    def __init__(self, message: str, outcome: Outcome = Outcome.PROTOCOL_FAILURE):
        super().__init__(message)
        self.outcome = outcome


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    """Stable JSON rendering used for anything that gets hashed."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_keys(payload: Dict[str, Any], allowed: Iterable[str], where: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise FixtureError(
            "unknown key(s) in {}: {}".format(where, ", ".join(unknown))
        )
    missing = sorted(set(allowed) - set(payload))
    if missing:
        raise FixtureError(
            "missing key(s) in {}: {}".format(where, ", ".join(missing))
        )


def _rel_posix(value: str, where: str) -> str:
    if not value or value.startswith("/") or ".." in Path(value).parts:
        raise FixtureError("{} must be a non-absolute relative path: {!r}".format(where, value))
    if "\\" in value:
        raise FixtureError("{} must use forward slashes: {!r}".format(where, value))
    return value


@dataclass(frozen=True)
class SourceFile:
    """One bound source file."""

    path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class SourceSpec:
    """Provenance of the code a fixture was captured from."""

    repository: str
    head: str
    manifest_sha256: str
    files: Tuple[SourceFile, ...]
    writable_prefixes: Tuple[str, ...] = ()

    @property
    def paths(self) -> Tuple[str, ...]:
        return tuple(f.path for f in self.files)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repository": self.repository,
            "head": self.head,
            "manifest_sha256": self.manifest_sha256,
            "files": [f.to_dict() for f in self.files],
            "writable_prefixes": list(self.writable_prefixes),
        }


@dataclass(frozen=True)
class Acceptance:
    """Allow-listed acceptance commands and content assertions.

    ``targeted``/``broader`` are argv vectors, never shell strings. Two
    placeholders are substituted at run time:

    ``${PYTHON}``    the interpreter running the harness
    ``${WORKTREE}``  the disposable evaluation worktree root
    """

    targeted: Tuple[Tuple[str, ...], ...] = ()
    broader: Tuple[Tuple[str, ...], ...] = ()
    allow_programs: Tuple[str, ...] = ()
    required_answer_contains: Tuple[str, ...] = ()
    required_files_mentioned: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "targeted": [list(c) for c in self.targeted],
            "broader": [list(c) for c in self.broader],
            "allow_programs": list(self.allow_programs),
            "required_answer_contains": list(self.required_answer_contains),
            "required_files_mentioned": list(self.required_files_mentioned),
        }


@dataclass(frozen=True)
class RealTask:
    """A frozen, reproducible repository task."""

    task_id: str
    task_family: str
    title: str
    problem: str
    deliverable: str
    constraints: Tuple[str, ...]
    relevant_source_files: Tuple[str, ...]
    source: SourceSpec
    acceptance: Acceptance
    fixture_version: int
    root: Path
    known_regression: Optional[str] = None
    prompt_source_files: Tuple[str, ...] = field(default=())

    @property
    def source_dir(self) -> Path:
        return self.root / "source"

    @property
    def tests_dir(self) -> Path:
        return self.root / "tests"

    @property
    def source_manifest_path(self) -> Path:
        return self.root / "source-manifest.json"

    @property
    def task_path(self) -> Path:
        return self.root / "task.json"

    def manifest_sha256(self) -> str:
        """SHA-256 over the verbatim bytes of ``task.json``."""
        return sha256_file(self.task_path)

    def snapshot_sha256(self) -> str:
        """SHA-256 over the frozen source snapshot, independent of ``task.json``."""
        digest = hashlib.sha256()
        for path in sorted(self.source.paths):
            target = self.source_dir / path
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(sha256_file(target).encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    def fixture_sha256(self) -> str:
        """Single identity for the whole frozen fixture."""
        return sha256_bytes(
            (self.manifest_sha256() + self.snapshot_sha256()).encode("ascii")
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_TASK,
            "schema_version": TASK_SCHEMA_VERSION,
            "task_id": self.task_id,
            "task_family": self.task_family,
            "fixture_version": self.fixture_version,
            "title": self.title,
            "deliverable": self.deliverable,
            "problem": self.problem,
            "constraints": list(self.constraints),
            "relevant_source_files": list(self.relevant_source_files),
            "prompt_source_files": list(self.prompt_source_files),
            "known_regression": self.known_regression,
            "source": self.source.to_dict(),
            "acceptance": self.acceptance.to_dict(),
        }

    def prompt_files(self) -> Tuple[str, ...]:
        """Source files shown to the model for this task."""
        if self.prompt_source_files:
            return self.prompt_source_files
        return self.relevant_source_files


def _load_argv_list(raw: Any, where: str) -> Tuple[Tuple[str, ...], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise FixtureError("{} must be a list of argv vectors".format(where))
    commands: List[Tuple[str, ...]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, list) or not entry:
            raise FixtureError(
                "{}[{}] must be a non-empty list of strings".format(where, index)
            )
        for token in entry:
            if not isinstance(token, str) or not token:
                raise FixtureError(
                    "{}[{}] must contain only non-empty strings".format(where, index)
                )
        commands.append(tuple(entry))
    return tuple(commands)


def _load_str_tuple(raw: Any, where: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise FixtureError("{} must be a list of strings".format(where))
    for item in raw:
        if not isinstance(item, str) or not item:
            raise FixtureError("{} must contain only non-empty strings".format(where))
    return tuple(raw)


def load_task(task_json: Path) -> RealTask:
    """Load and fully validate one fixture manifest."""
    task_json = Path(task_json)
    if not task_json.is_file():
        raise FixtureError("task manifest not found: {}".format(task_json))
    try:
        payload = json.loads(task_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureError("task manifest is not valid JSON: {}".format(exc)) from exc
    if not isinstance(payload, dict):
        raise FixtureError("task manifest must be a JSON object")

    if payload.get("schema") != SCHEMA_TASK:
        raise FixtureError(
            "task manifest schema must be {!r}, got {!r}".format(
                SCHEMA_TASK, payload.get("schema")
            )
        )
    if payload.get("schema_version") != TASK_SCHEMA_VERSION:
        raise FixtureError(
            "unsupported task schema_version {!r}".format(payload.get("schema_version"))
        )
    _require_keys(payload, _TASK_KEYS, "task.json")

    task_family = payload["task_family"]
    if task_family not in TASK_FAMILIES:
        raise FixtureError(
            "unknown task_family {!r}; expected one of {}".format(
                task_family, ", ".join(TASK_FAMILIES)
            )
        )
    deliverable = payload["deliverable"]
    if deliverable not in DELIVERABLES:
        raise FixtureError(
            "unknown deliverable {!r}; expected one of {}".format(
                deliverable, ", ".join(DELIVERABLES)
            )
        )

    raw_source = payload["source"]
    if not isinstance(raw_source, dict):
        raise FixtureError("task.json source must be an object")
    _require_keys(raw_source, _SOURCE_KEYS, "task.json source")
    raw_files = raw_source["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise FixtureError("task.json source.files must be a non-empty list")
    files: List[SourceFile] = []
    seen_paths = set()
    for index, entry in enumerate(raw_files):
        if not isinstance(entry, dict):
            raise FixtureError("source.files[{}] must be an object".format(index))
        _require_keys(entry, _FILE_KEYS, "source.files[{}]".format(index))
        rel = _rel_posix(entry["path"], "source.files[{}].path".format(index))
        if rel in seen_paths:
            raise FixtureError("duplicate source file path: {}".format(rel))
        seen_paths.add(rel)
        sha = entry["sha256"]
        if not isinstance(sha, str) or len(sha) != 64:
            raise FixtureError("source.files[{}].sha256 must be 64 hex chars".format(index))
        size = entry["size_bytes"]
        if not isinstance(size, int) or size < 0:
            raise FixtureError("source.files[{}].size_bytes must be a non-negative int".format(index))
        files.append(SourceFile(path=rel, sha256=sha, size_bytes=size))

    relevant = _rel_tuple(payload["relevant_source_files"], "relevant_source_files")
    unknown_relevant = sorted(set(relevant) - seen_paths)
    if unknown_relevant:
        raise FixtureError(
            "relevant_source_files not present in source.files: {}".format(
                ", ".join(unknown_relevant)
            )
        )
    prompt_files = _rel_tuple(payload["prompt_source_files"], "prompt_source_files")
    unknown_prompt = sorted(set(prompt_files) - seen_paths)
    if unknown_prompt:
        raise FixtureError(
            "prompt_source_files not present in source.files: {}".format(
                ", ".join(unknown_prompt)
            )
        )

    raw_acceptance = payload["acceptance"]
    if not isinstance(raw_acceptance, dict):
        raise FixtureError("task.json acceptance must be an object")
    _require_keys(raw_acceptance, _ACCEPTANCE_KEYS, "task.json acceptance")
    acceptance = Acceptance(
        targeted=_load_argv_list(raw_acceptance["targeted"], "acceptance.targeted"),
        broader=_load_argv_list(raw_acceptance["broader"], "acceptance.broader"),
        allow_programs=_load_str_tuple(
            raw_acceptance["allow_programs"], "acceptance.allow_programs"
        ),
        required_answer_contains=_load_str_tuple(
            raw_acceptance["required_answer_contains"], "acceptance.required_answer_contains"
        ),
        required_files_mentioned=_load_str_tuple(
            raw_acceptance["required_files_mentioned"], "acceptance.required_files_mentioned"
        ),
    )
    if not acceptance.targeted:
        raise FixtureError(
            "acceptance.targeted must declare at least one command; a fixture "
            "without executable acceptance cannot produce evidence"
        )

    writable = _rel_tuple(
        raw_source.get("writable_prefixes"), "source.writable_prefixes"
    )

    source_spec = SourceSpec(
        repository=raw_source["repository"],
        head=raw_source["head"],
        manifest_sha256=raw_source["manifest_sha256"],
        files=tuple(files),
        writable_prefixes=tuple(w for w in writable if w.endswith("/")),
    )

    return RealTask(
        task_id=payload["task_id"],
        task_family=task_family,
        title=payload["title"],
        problem=payload["problem"],
        deliverable=deliverable,
        constraints=_load_str_tuple(payload["constraints"], "constraints"),
        relevant_source_files=relevant,
        source=source_spec,
        acceptance=acceptance,
        fixture_version=payload["fixture_version"],
        root=task_json.parent.resolve(),
        known_regression=payload["known_regression"],
        prompt_source_files=prompt_files,
    )


def _rel_tuple(raw: Any, where: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise FixtureError("{} must be a list of relative paths".format(where))
    out: List[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str):
            raise FixtureError("{}[{}] must be a string".format(where, index))
        out.append(_rel_posix(item, "{}[{}]".format(where, index)))
    return tuple(out)


def load_source_manifest(task: RealTask) -> Dict[str, Any]:
    """Load ``source-manifest.json`` and verify it matches the task manifest."""
    path = task.source_manifest_path
    if not path.is_file():
        raise FixtureError(
            "source manifest not found: {}".format(path), Outcome.SOURCE_MISMATCH
        )
    actual = sha256_file(path)
    expected = task.source.manifest_sha256
    if actual != expected:
        raise FixtureError(
            "source-manifest.json sha256 {} != task.json source.manifest_sha256 {}".format(
                actual, expected
            ),
            Outcome.SOURCE_MISMATCH,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureError(
            "source-manifest.json is not valid JSON: {}".format(exc),
            Outcome.SOURCE_MISMATCH,
        ) from exc
    if payload.get("schema") != SCHEMA_SOURCE_MANIFEST:
        raise FixtureError(
            "source manifest schema must be {!r}".format(SCHEMA_SOURCE_MANIFEST),
            Outcome.SOURCE_MISMATCH,
        )
    manifest_files = payload.get("files")
    if not isinstance(manifest_files, list):
        raise FixtureError(
            "source-manifest.json files must be a list", Outcome.SOURCE_MISMATCH
        )
    by_path = {}
    for entry in manifest_files:
        if not isinstance(entry, dict) or "path" not in entry:
            raise FixtureError(
                "source-manifest.json file entries need a path", Outcome.SOURCE_MISMATCH
            )
        by_path[entry["path"]] = entry
    declared = {f.path: f for f in task.source.files}
    if set(by_path) != set(declared):
        raise FixtureError(
            "source-manifest.json file set differs from task.json source.files",
            Outcome.SOURCE_MISMATCH,
        )
    for path_key, entry in by_path.items():
        expected_file = declared[path_key]
        if entry.get("sha256") != expected_file.sha256:
            raise FixtureError(
                "source-manifest.json sha256 disagrees with task.json for {}".format(path_key),
                Outcome.SOURCE_MISMATCH,
            )
        if entry.get("size_bytes") != expected_file.size_bytes:
            raise FixtureError(
                "source-manifest.json size disagrees with task.json for {}".format(path_key),
                Outcome.SOURCE_MISMATCH,
            )
    return payload


def iter_fixture_manifests(tasks_root: Path) -> List[Path]:
    """All ``task.json`` paths under ``tasks_root``, sorted for determinism."""
    root = Path(tasks_root)
    if not root.is_dir():
        return []
    return sorted(root.glob("*/task.json"))


def load_task_by_id(task_id: str, tasks_root: Path) -> RealTask:
    manifest = Path(tasks_root) / task_id / "task.json"
    if not manifest.is_file():
        known = [p.parent.name for p in iter_fixture_manifests(Path(tasks_root))]
        raise FixtureError(
            "unknown task_id {!r}; available: {}".format(task_id, ", ".join(known) or "(none)")
        )
    return load_task(manifest)


def verify_snapshot_present(task: RealTask) -> None:
    """Fail closed when the fixture's own snapshot is incomplete."""
    for entry in task.source.files:
        target = task.source_dir / entry.path
        if not target.is_file():
            raise FixtureError(
                "fixture snapshot missing declared source file {}".format(entry.path),
                Outcome.SOURCE_MISMATCH,
            )


def summarize_task(task: RealTask) -> Dict[str, Any]:
    """Compact, evidence-safe description of a task (no source content)."""
    return {
        "task_id": task.task_id,
        "task_family": task.task_family,
        "deliverable": task.deliverable,
        "fixture_version": task.fixture_version,
        "repository": task.source.repository,
        "head": task.source.head,
        "fixture_sha256": task.fixture_sha256(),
        "manifest_sha256": task.manifest_sha256(),
        "snapshot_sha256": task.snapshot_sha256(),
        "source_files": list(task.source.paths),
        "targeted_commands": [list(c) for c in task.acceptance.targeted],
        "broader_commands": [list(c) for c in task.acceptance.broader],
    }


def iter_commands(acceptance: Acceptance) -> Sequence[Tuple[str, ...]]:
    return tuple(acceptance.targeted) + tuple(acceptance.broader)
