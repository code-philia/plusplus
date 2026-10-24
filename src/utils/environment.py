"""Load project model settings without depending on the CLI."""
from __future__ import annotations

import os
from pathlib import Path


def environment_file() -> Path:
    configured = os.environ.get('ARC_ENV_FILE', '').strip()
    if configured:
        return Path(configured).expanduser()
    local = Path.cwd() / '.env'
    if local.is_file():
        return local
    source_root = Path(__file__).resolve().parents[2]
    return source_root / '.env' if (source_root / 'pyproject.toml').is_file() else local


def ensure_dotenv_loaded() -> None:
    from dotenv import load_dotenv
    path = environment_file()
    if path.is_file():
        load_dotenv(path, override=False, encoding="utf-8-sig")
