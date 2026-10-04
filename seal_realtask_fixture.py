#!/usr/bin/env python3
"""Freeze a RealTask fixture: seal source hashes into the manifests.

Run this after editing a fixture's ``source/`` snapshot or its ``tests/``:

    python3 seal_realtask_fixture.py realtask/tasks/auto_ingest_plan_shorts_live_driver

It recomputes every declared file's SHA-256, writes ``source-manifest.json``,
and stamps ``task.json`` with the manifest hash. Both are required for
:func:`realtask.fixtures.load_source_manifest` to accept the fixture, so a
fixture cannot drift from its own hashes without failing closed.

The ``--check`` mode verifies without writing, which is what CI and
``realtime_bench.py validate`` rely on.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from realtask.fixtures import sha256_file, sha256_bytes  # noqa: E402
from realtask.version import SCHEMA_SOURCE_MANIFEST, SCHEMA_TASK  # noqa: E402


def seal(root: Path, captured_at: str, write: bool) -> int:
    root = Path(root)
    task_path = root / "task.json"
    if not task_path.is_file():
        print("no task.json in {}".format(root), file=sys.stderr)
        return 2
    payload: Dict[str, Any] = json.loads(task_path.read_text(encoding="utf-8"))
    source = payload.get("source") or {}
    declared = [entry["path"] for entry in source.get("files", [])]

    snapshot_root = root / "source"
    rows: List[Dict[str, Any]] = []
    problems: List[str] = []
    for rel in declared:
        target = snapshot_root / rel
        if not target.is_file():
            problems.append("declared source file missing from snapshot: {}".format(rel))
            rows.append({"path": rel, "sha256": "", "size_bytes": 0})
            continue
        rows.append(
            {
                "path": rel,
                "sha256": sha256_file(target),
                "size_bytes": target.stat().st_size,
            }
        )

    if problems:
        for problem in problems:
            print("error: {}".format(problem), file=sys.stderr)
        return 1

    manifest = {
        "schema": SCHEMA_SOURCE_MANIFEST,
        "repository": source.get("repository", ""),
        "head": source.get("head", ""),
        "captured_at": captured_at,
        "capture_note": (
            "Bounded snapshot of the files this fixture binds. Produced by "
            "reading the named checkout; the fixture never depends on that "
            "checkout remaining available or unmodified."
        ),
        "root_dir": "source",
        "files": rows,
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    manifest_sha = sha256_bytes(manifest_bytes)

    payload.setdefault("schema", SCHEMA_TASK)
    source["manifest_sha256"] = manifest_sha
    source["files"] = rows
    task_bytes = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"

    manifest_path = root / "source-manifest.json"
    if write:
        manifest_path.write_bytes(manifest_bytes)
        task_path.write_bytes(task_bytes)
        print("sealed {}".format(root))
        print("  manifest_sha256 {}".format(manifest_sha))
        for row in rows:
            print("  {} {}".format(row["sha256"], row["path"]))
        return 0

    drift: List[str] = []
    if not manifest_path.is_file():
        drift.append("source-manifest.json is missing")
    elif manifest_path.read_bytes() != manifest_bytes:
        drift.append("source-manifest.json does not match the snapshot")
    if task_path.read_bytes() != task_bytes:
        drift.append("task.json hashes are stale")
    for problem in drift:
        print("DRIFT {}: {}".format(root, problem), file=sys.stderr)
    return 1 if drift else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fixture", type=Path, help="fixture directory")
    parser.add_argument(
        "--captured-at", default="2026-10-02T00:00:00Z",
        help="provenance timestamp recorded in source-manifest.json",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="verify the fixture is sealed; write nothing and exit non-zero on drift",
    )
    args = parser.parse_args(argv)
    return seal(args.fixture, args.captured_at, write=not args.check)


if __name__ == "__main__":
    sys.exit(main())
