"""UTF-8 boundaries for terminal output and Python child processes."""
from __future__ import annotations

import sys
from typing import Mapping


def utf8_environment(environment: Mapping[str, str]) -> dict[str, str]:
    result = dict(environment)
    result.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    return result


def configure_utf8_console() -> None:
    """Configure real terminal/redirected streams without replacing capture streams."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(encoding='utf-8', errors='strict' if stream is sys.stdin else 'backslashreplace')
            except (OSError, ValueError):
                # An embedding host can own a closed or already-read stream.
                pass
