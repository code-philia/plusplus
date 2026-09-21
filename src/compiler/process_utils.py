"""Small cross-platform helpers for running generated-project commands."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from typing import Any, Mapping


def resolve_executable(name: str, environment: Mapping[str, str]) -> str | None:
    """Resolve a command using the child process environment's PATH."""

    return shutil.which(name, path=environment.get("PATH"))


def process_group_kwargs() -> dict[str, Any]:
    """Return Popen options that isolate a command and its child processes."""

    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_process_tree(
    process: subprocess.Popen[Any],
    *,
    grace_seconds: float = 2.0,
) -> None:
    """Terminate a command and its descendants on Windows and POSIX systems."""

    if process.poll() is not None:
        return

    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill is not None:
            try:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5.0,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def run_command(
    command: list[str],
    *,
    cwd: str | os.PathLike[str],
    environment: Mapping[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run a captured command while ensuring timeout cleanup includes children."""

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **process_group_kwargs(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_process_tree(process)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout or getattr(exc, "output", None),
            stderr=stderr or getattr(exc, "stderr", None),
        ) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
