"""Run-directory evidence layout with atomic writes.

Every write goes to a temporary file in the destination directory, is flushed
and fsynced, then renamed into place, and the containing directory is fsynced
too. A reader therefore never observes a half-written artifact, and a crashed
run leaves either the previous file or the complete new one.

Atomic-write pattern adapted from ``fixgit_repro_v1.py`` in this repository,
which was the only script here that had one.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .version import SCHEMA_RUN_MANIFEST, SCHEMA_SOURCE_MANIFEST


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=str(path.parent), prefix="." + path.name + "-", delete=False
    )
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, str(path))
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(Path(path), text.encode("utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(Path(path), json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


class RunDirectory:
    """The evidence tree for one harness invocation.

    Layout::

        <run_id>/
            manifest.json          harness + run provenance
            task.json               the frozen fixture, verbatim
            source-manifest.json    binding identity + verification result
            single/                 per-attempt role payloads
            scout/
            implementer/
            reviewer/
            patch.diff              the exact candidate text, verbatim
            test-results.json       every allow-listed command and its output
            metrics.json            component metrics, all attempts
            comparison.json         single vs swarm, components only
    """

    ROLE_DIRS = ("single", "scout", "implementer", "reviewer", "swarm")

    def __init__(self, base: Path, run_id: str, prefix: str = ""):
        """``prefix`` nests this run's artifacts under a subdirectory.

        A single-task run uses the flat documented layout. A multi-task run gives
        each task its own subdirectory so one task's evidence can never overwrite
        another's.
        """
        self.run_id = run_id
        self.prefix = prefix
        self.path = Path(base) / run_id / prefix if prefix else Path(base) / run_id
        self.path.mkdir(parents=True, exist_ok=True)
        for name in self.ROLE_DIRS:
            (self.path / name).mkdir(exist_ok=True)
        self._written: List[str] = []

    # -- paths -------------------------------------------------------------

    def role_dir(self, role: str) -> Path:
        if role not in self.ROLE_DIRS:
            raise ValueError("unknown role directory {!r}".format(role))
        target = self.path / role
        target.mkdir(exist_ok=True)
        return target

    def file(self, name: str) -> Path:
        return self.path / name

    def role_file(self, role: str, name: str) -> Path:
        return self.role_dir(role) / name

    # -- writes ------------------------------------------------------------

    def _record(self, relative: str) -> None:
        qualified = "{}{}".format(self.prefix, relative)
        if qualified not in self._written:
            self._written.append(qualified)

    def write_json(self, relative: str, payload: Any) -> Path:
        target = self.file(relative)
        atomic_write_json(target, payload)
        self._record(relative)
        return target

    def write_text(self, relative: str, text: str) -> Path:
        target = self.file(relative)
        atomic_write_text(target, text)
        self._record(relative)
        return target

    def write_bytes(self, relative: str, payload: bytes) -> Path:
        target = self.file(relative)
        atomic_write_bytes(target, payload)
        self._record(relative)
        return target

    def write_role_json(self, role: str, name: str, payload: Any) -> Path:
        target = self.role_file(role, name)
        atomic_write_json(target, payload)
        self._record("{}/{}".format(role, name))
        return target

    def write_role_text(self, role: str, name: str, text: str) -> Path:
        target = self.role_file(role, name)
        atomic_write_text(target, text)
        self._record("{}/{}".format(role, name))
        return target

    def copy_fixture(self, task_json: Path, source_manifest: Path) -> None:
        atomic_write_bytes(self.file("task.json"), Path(task_json).read_bytes())
        self._record("task.json")
        atomic_write_bytes(
            self.file("source-manifest.json"), Path(source_manifest).read_bytes()
        )
        self._record("source-manifest.json")

    # -- manifest ----------------------------------------------------------

    def write_run_manifest(
        self,
        *,
        harness_sha: str,
        harness_dirty: bool,
        fixture_sha256: str,
        task_id: str,
        task_family: str,
        endpoints: Sequence[Dict[str, Any]],
        strategies: Sequence[str],
        argv: Sequence[str],
        host: Dict[str, Any],
        options: Dict[str, Any],
        started_at: str,
    ) -> Path:
        payload = {
            "schema": SCHEMA_RUN_MANIFEST,
            "run_id": self.run_id,
            "harness": {
                "name": "realtask",
                "git_sha": harness_sha,
                "git_dirty": harness_dirty,
            },
            "fixture": {
                "task_id": task_id,
                "task_family": task_family,
                "fixture_sha256": fixture_sha256,
            },
            "model_runtime": list(endpoints),
            "strategies": list(strategies),
            "argv": list(argv),
            "host": dict(host),
            "options": dict(options),
            "started_at": started_at,
            "manifest_written_at": utc_now(),
            "authority": {
                "authoritative_repo_mutated": False,
                "assistx_task_state_mutated": False,
                "routing_or_admission_mutated": False,
                "note": "The harness runs experiments and writes evidence only.",
            },
            "artifacts_written": [],
        }
        target = self.write_json("manifest.json", payload)
        return target

    def finalize_manifest(self, extra: Optional[Dict[str, Any]] = None) -> Path:
        """Index the run by walking the tree, so nested task evidence is included."""
        target = self.file("manifest.json")
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["artifacts_written"] = sorted(
            str(p.relative_to(self.path)) for p in self.path.rglob("*") if p.is_file()
        )
        payload["finalized_at"] = utc_now()
        if extra:
            payload.update(extra)
        return self.write_json("manifest.json", payload)

    def tree(self) -> List[str]:
        return sorted(self._written)


def harness_provenance(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """agent-harness git SHA and dirty flag for evidence provenance."""
    import subprocess

    root = Path(repo_root) if repo_root else Path(__file__).resolve().parent.parent
    sha = ""
    dirty = False
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if completed.returncode == 0:
            sha = completed.stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        dirty = bool(status.stdout.strip())
    except (OSError, Exception):  # noqa: BLE001 - provenance is best effort
        pass
    return {"git_sha": sha, "git_dirty": dirty, "repo_root": str(root)}


def source_manifest_artifact(
    source_manifest_payload: Dict[str, Any], binding, verification: Dict[str, Any]
) -> Dict[str, Any]:
    """The ``source-manifest.json`` written into a run directory.

    It carries the frozen manifest verbatim plus the verification verdict, so a
    reader can confirm which bytes were used and whether they matched.
    """
    return {
        "schema": SCHEMA_SOURCE_MANIFEST,
        "manifest": source_manifest_payload,
        "verification": verification,
        "binding": binding.to_dict() if binding is not None else None,
    }
