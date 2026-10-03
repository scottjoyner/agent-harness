"""Disposable evaluation worktree and allow-listed command execution.

The authoritative source tree is read-only to this harness in the strongest
possible sense: it is never opened for writing at all. A candidate patch is
applied to a throwaway copy created under the harness-owned work directory,
allow-listed tests run there, and the copy is then discarded.

Commands are argv vectors, never shell strings. The program of each argv must
resolve to the interpreter running the harness or to an explicitly allow-listed
program. There is no shell, no glob expansion and no redirection.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .fixtures import RealTask
from .patch import ApplyResult, syntax_check

#: Where the evaluation copy of each fixture's source lands inside a worktree.
TESTS_DIRNAME = "_realtask_tests"
ANSWER_FILENAME = "_realtask_answer.txt"
SKIP_DIR_NAMES = {"__pycache__", ".git", ".pytest_cache"}


class WorktreeGuardError(RuntimeError):
    """The harness refused to build an evaluation worktree somewhere unsafe."""


@dataclass(frozen=True)
class CommandResult:
    argv: Tuple[str, ...]
    program: str
    returncode: int
    duration_s: float
    timed_out: bool
    stdout: str
    stderr: str
    allowed: bool
    rejection: str = ""

    @property
    def passed(self) -> bool:
        return self.allowed and not self.timed_out and self.returncode == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "argv": list(self.argv),
            "program": self.program,
            "returncode": self.returncode,
            "duration_s": self.duration_s,
            "timed_out": self.timed_out,
            "passed": self.passed,
            "allowed": self.allowed,
            "rejection": self.rejection,
            "stdout": self.stdout[-8000:],
            "stderr": self.stderr[-8000:],
        }


def render_command(argv: Sequence[str], worktree: Path) -> List[str]:
    """Substitute the permitted placeholders."""
    out: List[str] = []
    for token in argv:
        out.append(
            token.replace("${PYTHON}", sys.executable)
            .replace("${WORKTREE}", str(worktree))
            .replace("${ANSWER}", str(worktree / ANSWER_FILENAME))
        )
    return out


def _program_key(program: str) -> str:
    return Path(program).name


class ProgramPolicy:
    """Decides which programs an acceptance command may invoke."""

    def __init__(self, extra_allowed: Sequence[str] = ()):
        self._allowed: Set[str] = {_program_key(sys.executable), "python3", "python"}
        for name in extra_allowed:
            self._allowed.add(_program_key(name))
        self._resolved: Dict[str, Optional[str]] = {}

    def resolve(self, program: str) -> Optional[str]:
        if program in self._resolved:
            return self._resolved[program]
        resolved: Optional[str]
        if Path(program).is_absolute():
            resolved = program if os.access(program, os.X_OK) else None
        else:
            resolved = shutil.which(program)
        self._resolved[program] = resolved
        return resolved

    def permits(self, program: str) -> Tuple[bool, str]:
        if _program_key(program) not in self._allowed:
            return False, "program {!r} is not on the fixture's allow list".format(program)
        if self.resolve(program) is None:
            return False, "program {!r} could not be resolved on PATH".format(program)
        return True, ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed_programs": sorted(self._allowed),
            "resolved": {k: v for k, v in sorted(self._resolved.items())},
        }


@dataclass
class EvaluationWorktree:
    """A throwaway copy of the fixture source plus its bounded tests."""

    task: RealTask
    root: Path
    known_paths: Tuple[str, ...]
    writable_prefixes: Tuple[str, ...]
    policy: ProgramPolicy
    _origin_guards: Tuple[Path, ...] = field(default=())
    _closed: bool = False

    @classmethod
    def create(
        cls,
        task: RealTask,
        work_root: Path,
        *,
        extra_allowed_programs: Sequence[str] = (),
        guard_paths: Sequence[Path] = (),
    ) -> "EvaluationWorktree":
        guards: List[Path] = []
        for path in [task.root, task.source_dir, *guard_paths]:
            resolved = Path(path).resolve()
            if resolved not in guards:
                guards.append(resolved)

        work_root = Path(work_root)
        work_root.mkdir(parents=True, exist_ok=True)
        dest = (work_root / "eval").resolve()
        if any(_is_within(dest, guard) for guard in guards):
            raise WorktreeGuardError(
                "evaluation worktree {} is inside a read-only tree {}".format(dest, guards)
            )
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)

        shutil.copytree(task.source_dir, dest, dirs_exist_ok=True)

        tests_src = task.tests_dir
        if tests_src.is_dir():
            shutil.copytree(tests_src, dest / TESTS_DIRNAME)
        (dest / TESTS_DIRNAME).mkdir(parents=True, exist_ok=True)

        policy = ProgramPolicy(extra_allowed_programs)
        return cls(
            task=task,
            root=dest,
            known_paths=tuple(task.source.paths),
            writable_prefixes=tuple(task.source.writable_prefixes),
            policy=policy,
            _origin_guards=tuple(guards),
        )

    # -- patch application -------------------------------------------------

    def hashes(self) -> Dict[str, str]:
        """SHA-256 of every file currently in the worktree.

        Whole-tree fingerprinting rather than a declared-path list, so a patch
        that creates a new file is still detected as a change.
        """
        import hashlib

        out: Dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root)
            if any(part in SKIP_DIR_NAMES for part in rel.parts):
                continue
            if rel.name.endswith(".pyc"):
                continue
            out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out

    def write_answer(self, text: str) -> Path:
        """Expose the candidate's own answer text to fixture-provided checkers."""
        target = self.root / ANSWER_FILENAME
        target.write_text(text, encoding="utf-8")
        return target

    def apply_patch(self, patch_text: str, applier: str = "auto") -> ApplyResult:
        """Apply ``patch_text`` inside this worktree only."""
        from .patch import normalize_patch, screen_patch

        patch_text = normalize_patch(patch_text)
        before = self.hashes()
        safety = screen_patch(patch_text, self.known_paths, self.writable_prefixes)
        if not safety.ok:
            hint = "INVALID_PATCH" if safety.binary else "INVALID_PATCH"
            return ApplyResult(
                ok=False,
                outcome_hint=hint,
                reason=safety.reason or "patch rejected by safety screen",
                before_hashes=before,
                after_hashes=before,
            )

        chosen = applier
        if applier == "auto":
            chosen = "git" if shutil.which("git") else "patch"

        if chosen == "git":
            parse = self._run_tool(["git", "apply", "--numstat", "-p1"], patch_text)
            if parse.returncode != 0:
                return ApplyResult(
                    ok=False,
                    outcome_hint="INVALID_PATCH",
                    reason="patch is not a well-formed unified diff",
                    applier="git",
                    stdout=parse.stdout,
                    stderr=parse.stderr,
                    before_hashes=before,
                    after_hashes=before,
                )
            check = self._run_tool(["git", "apply", "--check", "-p1"], patch_text)
            if check.returncode != 0:
                return ApplyResult(
                    ok=False,
                    outcome_hint="PATCH_DOES_NOT_APPLY",
                    reason="patch parsed but does not apply to the bound source",
                    applier="git",
                    stdout=check.stdout,
                    stderr=check.stderr,
                    before_hashes=before,
                    after_hashes=before,
                )
            applied = self._run_tool(["git", "apply", "-p1"], patch_text)
        else:
            check = self._run_tool(["patch", "--dry-run", "-p1"], patch_text)
            if check.returncode != 0:
                blob = (check.stdout + check.stderr).lower()
                hint = (
                    "PATCH_DOES_NOT_APPLY"
                    if "does not apply" in blob or "hunk" in blob
                    else "INVALID_PATCH"
                )
                return ApplyResult(
                    ok=False,
                    outcome_hint=hint,
                    reason="patch dry-run failed",
                    applier="patch",
                    stdout=check.stdout,
                    stderr=check.stderr,
                    before_hashes=before,
                    after_hashes=before,
                )
            applied = self._run_tool(["patch", "-p1"], patch_text)

        if applied.returncode != 0:
            return ApplyResult(
                ok=False,
                outcome_hint="PATCH_DOES_NOT_APPLY",
                reason="patch application failed",
                applier=chosen,
                stdout=applied.stdout,
                stderr=applied.stderr,
                before_hashes=before,
                after_hashes=before,
            )

        after = self.hashes()
        return ApplyResult(
            ok=True,
            applier=chosen,
            stdout=applied.stdout,
            stderr=applied.stderr,
            before_hashes=before,
            after_hashes=after,
        )

    def _run_tool(self, argv: Sequence[str], patch_text: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["HOME"] = str(self.root)
        return subprocess.run(
            list(argv),
            input=patch_text,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )

    # -- evaluation --------------------------------------------------------

    def run_command(self, argv: Sequence[str], timeout_s: float = 300.0) -> CommandResult:
        rendered = render_command(argv, self.root)
        if not rendered:
            return CommandResult((), "", -1, 0.0, False, "", "", False, "empty command")
        program = rendered[0]
        permitted, rejection = self.policy.permits(program)
        if not permitted:
            return CommandResult(
                tuple(rendered), program, -1, 0.0, False, "", "", False, rejection
            )
        resolved = self.policy.resolve(program) or program
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONHASHSEED"] = "0"
        env["REALTASK_EVAL_ROOT"] = str(self.root)
        env.pop("REALTASK_ENDPOINT_BASE_URL", None)
        env.pop("REALTASK_ENDPOINT_MODEL", None)
        start = time.monotonic()
        try:
            completed = subprocess.run(
                [resolved] + rendered[1:],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                tuple(rendered),
                program,
                -1,
                time.monotonic() - start,
                True,
                _decode(exc.stdout),
                _decode(exc.stderr),
                True,
            )
        except OSError as exc:
            return CommandResult(
                tuple(rendered), program, -1, time.monotonic() - start, False, "", str(exc), True
            )
        return CommandResult(
            tuple(rendered),
            program,
            completed.returncode,
            time.monotonic() - start,
            False,
            completed.stdout,
            completed.stderr,
            True,
        )

    def syntax(self, paths: Sequence[str]) -> Tuple[bool, Dict[str, str]]:
        return syntax_check(self.root, paths)

    def close(self) -> None:
        if self._closed:
            return
        shutil.rmtree(self.root, ignore_errors=True)
        self._closed = True


def _is_within(candidate: Path, guard: Path) -> bool:
    try:
        candidate.relative_to(guard)
        return True
    except ValueError:
        return False


def _decode(payload: Optional[bytes]) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    return payload.decode("utf-8", errors="replace")
