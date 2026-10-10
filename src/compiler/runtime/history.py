from __future__ import annotations

import os
from pathlib import Path

from utils.process import resolve_executable
from utils.git import GitCommandError, run_git


class GitStageError(RuntimeError):
    pass


class ProjectGitHistory:
    """Commit only compiler-owned paths in the generated application."""

    def __init__(self, output_root: Path) -> None:
        self.root = output_root.expanduser().resolve()
        self.executable = resolve_executable("git", os.environ)

    def initialize(self) -> None:
        if (self.root / ".git").exists():
            raise GitStageError("Generated project already contains a Git repository.")
        self._run(["init"])

    def commit(self, stage: str, paths: list[str]) -> None:
        if not paths:
            raise GitStageError(f"No compiler-owned paths for {stage}.")
        self._run(["add", "-A", "--", *sorted(set(paths))])
        self._run([
            "-c", "user.name=ARC Compiler",
            "-c", "user.email=arc@local.invalid",
            "commit", "--allow-empty", "-m", f"ARC: {stage}",
        ])

    def _run(self, args: list[str]) -> str:
        try:
            return run_git(self.root, args, executable=self.executable)
        except GitCommandError as exc:
            raise GitStageError(str(exc)) from exc
