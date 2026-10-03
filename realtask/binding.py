"""Source binding: prove the benchmark is looking at the frozen code.

The harness never executes against whatever happens to be in a mutable
checkout. Before any model call it verifies three things:

1. the frozen manifest itself is intact (``source-manifest.json`` hashes to the
   value recorded in ``task.json``);
2. every declared source file is present and hashes to its recorded SHA-256;
3. the source HEAD matches.

Any failure yields :data:`~realtask.taxonomy.Outcome.SOURCE_MISMATCH`. There is
no "continue anyway" path, and no fallback to a different clone or mirror.

This is deliberately *consumption* of the source-binding concept only. It grants
no execution authority and performs no claiming: see
``auto-assist``/``auto-router`` for task authority. Nothing here writes to the
bound tree.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .fixtures import RealTask, load_source_manifest, sha256_file, verify_snapshot_present
from .taxonomy import Outcome

#: How the source root was resolved for this run.
MODE_SNAPSHOT = "snapshot"
MODE_EXTERNAL = "external_worktree"

#: How the HEAD requirement was discharged.
HEAD_RECORDED = "recorded_provenance"
HEAD_VERIFIED = "verified_against_git"
HEAD_NOT_REQUESTED = "not_requested"
HEAD_ABSENT = "fixture_declares_no_head"


@dataclass(frozen=True)
class FileCheck:
    path: str
    present: bool
    expected_sha256: str
    actual_sha256: Optional[str]
    expected_size_bytes: int
    actual_size_bytes: Optional[int]

    @property
    def ok(self) -> bool:
        return self.present and self.actual_sha256 == self.expected_sha256

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "present": self.present,
            "ok": self.ok,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "expected_size_bytes": self.expected_size_bytes,
            "actual_size_bytes": self.actual_size_bytes,
        }


@dataclass
class SourceBinding:
    """Result of verifying one fixture against one source root."""

    ok: bool
    mode: str
    source_root: str
    repository: str
    expected_head: str
    actual_head: Optional[str]
    head_status: str
    manifest_sha256: str
    files: List[FileCheck] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    outcome: Optional[Outcome] = None

    @property
    def source_binding_sha256(self) -> str:
        """Stable identity of the exact bytes the model was shown."""
        import hashlib

        digest = hashlib.sha256()
        digest.update(self.repository.encode("utf-8"))
        digest.update(b"\0")
        digest.update((self.expected_head or "").encode("utf-8"))
        for check in sorted(self.files, key=lambda c: c.path):
            digest.update(check.path.encode("utf-8"))
            digest.update(b"\0")
            digest.update((check.actual_sha256 or "").encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "source_root": self.source_root,
            "repository": self.repository,
            "expected_head": self.expected_head,
            "actual_head": self.actual_head,
            "head_status": self.head_status,
            "manifest_sha256": self.manifest_sha256,
            "source_binding_sha256": self.source_binding_sha256,
            "files": [c.to_dict() for c in self.files],
            "reasons": list(self.reasons),
            "outcome": self.outcome.value if self.outcome else None,
        }


def git_head(root: Path) -> Optional[str]:
    """``git -C root rev-parse HEAD`` or ``None`` when unavailable."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    head = completed.stdout.strip()
    return head or None


def _resolve_root(task: RealTask, source_root: Optional[Path]) -> Path:
    if source_root is None:
        return task.source_dir
    root = Path(source_root)
    if not root.is_dir():
        raise FileNotFoundError("source root is not a directory: {}".format(root))
    return root.resolve()


def verify_source_binding(
    task: RealTask,
    source_root: Optional[Path] = None,
    require_head: bool = False,
) -> SourceBinding:
    """Verify manifest integrity, file hashes, file presence, and HEAD.

    ``source_root`` selects the tree to check. ``None`` means the fixture's own
    frozen snapshot, which is the hermetic default.

    ``require_head`` forces a live ``git rev-parse HEAD`` comparison even in
    snapshot mode. Snapshot mode otherwise treats the recorded HEAD as
    provenance and says so explicitly rather than implying it was verified.
    """
    reasons: List[str] = []
    mode = MODE_SNAPSHOT if source_root is None else MODE_EXTERNAL
    root = _resolve_root(task, source_root)

    try:
        load_source_manifest(task)
        manifest_sha = task.source.manifest_sha256
    except Exception as exc:  # noqa: BLE001 - fail closed on any manifest problem
        reasons.append(str(exc))
        return SourceBinding(
            ok=False,
            mode=mode,
            source_root=str(root),
            repository=task.source.repository,
            expected_head=task.source.head,
            actual_head=None,
            head_status=HEAD_NOT_REQUESTED,
            manifest_sha256="",
            reasons=reasons,
            outcome=Outcome.SOURCE_MISMATCH,
        )

    try:
        verify_snapshot_present(task)
    except Exception as exc:  # noqa: BLE001
        reasons.append(str(exc))
        manifest_sha = task.source.manifest_sha256

    checks: List[FileCheck] = []
    for entry in task.source.files:
        target = root / entry.path
        present = target.is_file()
        actual_sha: Optional[str] = None
        actual_size: Optional[int] = None
        if present:
            try:
                actual_sha = sha256_file(target)
                actual_size = target.stat().st_size
            except OSError as exc:
                reasons.append("unreadable {}: {}".format(entry.path, exc))
        check = FileCheck(
            path=entry.path,
            present=present,
            expected_sha256=entry.sha256,
            actual_sha256=actual_sha,
            expected_size_bytes=entry.size_bytes,
            actual_size_bytes=actual_size,
        )
        checks.append(check)
        if not present:
            reasons.append("expected file absent: {}".format(entry.path))
        elif not check.ok:
            reasons.append(
                "hash mismatch for {}: expected {} got {}".format(
                    entry.path, entry.sha256, actual_sha
                )
            )

    expected_head = task.source.head or ""
    actual_head: Optional[str] = None
    if not expected_head:
        head_status = HEAD_ABSENT
    elif mode == MODE_EXTERNAL:
        actual_head = git_head(root)
        head_status = HEAD_VERIFIED
        if actual_head is None:
            reasons.append(
                "external source root is not a git checkout, cannot verify HEAD"
            )
        elif actual_head != expected_head:
            reasons.append(
                "source HEAD mismatch: expected {} got {}".format(expected_head, actual_head)
            )
    elif require_head:
        actual_head = git_head(root)
        head_status = HEAD_VERIFIED
        if actual_head is None:
            reasons.append(
                "require_head requested but source root is not a git checkout"
            )
        elif actual_head != expected_head:
            reasons.append(
                "source HEAD mismatch: expected {} got {}".format(expected_head, actual_head)
            )
    else:
        head_status = HEAD_RECORDED

    ok = not reasons
    return SourceBinding(
        ok=ok,
        mode=mode,
        source_root=str(root),
        repository=task.source.repository,
        expected_head=expected_head,
        actual_head=actual_head,
        head_status=head_status,
        manifest_sha256=manifest_sha,
        files=checks,
        reasons=reasons,
        outcome=None if ok else Outcome.SOURCE_MISMATCH,
    )


def binding_prompt_block(binding: SourceBinding) -> str:
    """Exact, verbatim source-binding text handed to the reviewer.

    The reviewer is told the resolved path, the repository, the HEAD, the HEAD
    verification status, and every per-file SHA-256, so a reviewer cannot
    silently reason about "some checkout". The harness re-verifies the binding
    immediately before invoking the reviewer and refuses to run on drift.
    """
    lines = [
        "SOURCE_BINDING (verified by the harness immediately before this call)",
        "  repository: {}".format(binding.repository),
        "  source_root: {}".format(binding.source_root),
        "  binding_mode: {}".format(binding.mode),
        "  expected_head: {}".format(binding.expected_head or "(none declared)"),
        "  actual_head: {}".format(binding.actual_head or "(not queried)"),
        "  head_status: {}".format(binding.head_status),
        "  source_binding_sha256: {}".format(binding.source_binding_sha256),
        "  files:",
    ]
    for check in sorted(binding.files, key=lambda c: c.path):
        lines.append(
            "    {} sha256={} status={}".format(
                check.path,
                check.actual_sha256 or "(absent)",
                "ok" if check.ok else "MISMATCH",
            )
        )
    return "\n".join(lines)
