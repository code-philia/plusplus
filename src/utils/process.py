"""Small cross-platform helpers for running generated-project commands."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from typing import Any, Mapping

from .encoding import utf8_environment


def resolve_executable(name: str, environment: Mapping[str, str]) -> str | None:
    """Resolve a command using the child process environment's PATH."""

    # Windows environment names are case-insensitive, even when callers pass
    # an ordinary dict with the common spelling "Path". POSIX remains sensitive.
    path = environment.get("PATH")
    if path is None and os.name == "nt":
        path = next((value for key, value in environment.items() if key.upper() == "PATH"), None)
    search_path = path if path is not None else os.defpath
    if os.name == 'nt' and name.lower() in {'npm', 'npx'}:
        shim = shutil.which(name + '.cmd', path=search_path)
        if shim is not None:
            return shim
    return shutil.which(name, path=search_path)


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
        # Windows npm/npx are batch shims, requiring cmd.exe. Native executables
        # and POSIX commands keep direct argument-list execution.
        shell=os.name == 'nt' and command[0].lower().endswith(('.cmd', '.bat')),
        cwd=cwd,
        env=utf8_environment(environment),
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
