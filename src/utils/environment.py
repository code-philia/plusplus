"""Load project model settings without depending on the CLI."""
from __future__ import annotations

import os
from pathlib import Path


def ensure_dotenv_loaded() -> None:
    from dotenv import load_dotenv
    path = Path(os.environ.get('ARC_ENV_FILE') or Path(__file__).resolve().parents[2] / '.env')
    if path.is_file():
        load_dotenv(path, override=False, encoding="utf-8-sig")
