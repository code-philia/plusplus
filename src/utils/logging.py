from __future__ import annotations

import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .encoding import configure_utf8_console


configure_utf8_console()


_current_run_id: ContextVar[str | None] = ContextVar("arc_run_id", default=None)
_log_lock = RLock()


def append_debug_log(text: str, *, workspace_root: str | os.PathLike[str] | None = None) -> None:
    """Append the same console text without ANSI color sequences."""
    import re
    explicit = os.environ.get('ARC_DEBUG_LOG_PATH', '').strip()
    root = workspace_root or os.environ.get('ARC_WORKSPACE_ROOT', '').strip()
    if root:
        path = Path(root).expanduser().resolve() / '.arc/debug.log'
    elif explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        return
    with _log_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(re.sub(r'\x1b\[[0-9;]*m', '', text) + '\n')


def set_run_id(run_id: str | None) -> None:
    """Retain the compiler-run context without displaying it in log prefixes."""

    _current_run_id.set(str(run_id).strip() if run_id else None)


def format_terminal_log(
    agent_name: str,
    message: str,
    *,
    status: str | None = None,
    node_id: str | None = None,
) -> str:
    timestamp = local_timestamp()
    prefix = f"[{timestamp}] [{agent_name}]"
    if node_id:
        prefix += f"[{node_id}]"
    if status:
        prefix += f"[{status}]"
    if not _should_color():
        return "\n".join(f"{prefix} {line}" for line in (str(message).splitlines() or [""]))

    prefix_color = _agent_color(agent_name, message, status)
    colored_prefix = f"{prefix_color}{prefix}{ANSI_RESET}"
    return "\n".join(
        f"{colored_prefix} {_color_message(line, status)}"
        for line in (str(message).splitlines() or [""])
    )


def write_terminal_log(
    agent_name: str,
    message: str,
    *,
    status: str | None = None,
    node_id: str | None = None,
    workspace_root: str | os.PathLike[str] | None = None,
) -> None:
    text = format_terminal_log(agent_name, message, status=status, node_id=node_id)
    with _log_lock:
        append_debug_log(text, workspace_root=workspace_root)
        print(text, flush=True)


class SynchronousLog:
    """Synchronize compiler progress to the console and workspace debug.log."""

    def __init__(self, agent_name: str, *, workspace_root: str | os.PathLike[str] | None = None) -> None:
        self._agent_name = agent_name
        self._workspace_root = str(workspace_root) if workspace_root is not None else None

    def info(self, message: str, *, node_id: str | None = None) -> None:
        self._write(message, status=None, node_id=node_id)

    def warning(self, message: str, *, node_id: str | None = None) -> None:
        self._write(message, status='warning', node_id=node_id)

    def error(self, message: str, *, node_id: str | None = None) -> None:
        self._write(message, status='error', node_id=node_id)

    def _write(self, message: str, *, status: str | None, node_id: str | None) -> None:
        write_terminal_log(self._agent_name, message, status=status, node_id=node_id,
                           workspace_root=self._workspace_root)


ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_RED = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_GRAY = "\033[90m"


def local_timestamp() -> str:
    return datetime.now(_log_timezone()).strftime("%m-%d %H:%M:%S")


def _log_timezone() -> tzinfo:
    configured = os.environ.get("ARC_TIMEZONE", "").strip() or os.environ.get("TZ", "").strip()
    if configured:
        try:
            return ZoneInfo(configured)
        except ZoneInfoNotFoundError:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def _should_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    forced = os.environ.get("ARC_LOG_COLOR", "").strip().lower()
    if forced in {"1", "true", "yes", "on"}:
        return True
    if forced in {"0", "false", "no", "off"}:
        return False
    return sys.stdout.isatty()


def _agent_color(agent_name: str, message: str, status: str | None) -> str:
    normalized = f"{agent_name} {message} {status or ''}".lower()
    if status == "error" or "failed" in normalized:
        return ANSI_RED + ANSI_BOLD
    if status == "warning":
        return ANSI_YELLOW + ANSI_BOLD
    if agent_name in {"System", "Compiler"}:
        return ANSI_GRAY + ANSI_BOLD
    return ANSI_BOLD


def _color_message(message: str, status: str | None) -> str:
    if status == "error":
        return f"{ANSI_RED}{message}{ANSI_RESET}"
    if status == "warning":
        return f"{ANSI_YELLOW}{message}{ANSI_RESET}"
    return message
