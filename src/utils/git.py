"""Git command execution without compilation-stage policy."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from .process import resolve_executable, run_command


class GitCommandError(RuntimeError):
    pass


def run_git(root: Path, args: Sequence[str], *, executable: str | None = None) -> str:
    executable = executable or resolve_executable("git", os.environ)
    if executable is None:
        raise GitCommandError("git is required to compile the generated project.")
    try:
        result = run_command([executable, *args], cwd=root,
                             environment=os.environ, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitCommandError(f"git {args[0]} failed: {exc}") from exc
    if result.returncode:
        raise GitCommandError(f"git {args[0]} failed: {result.stderr or result.stdout}")
    return result.stdout.strip()
