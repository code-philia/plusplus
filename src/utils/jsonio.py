from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(payload, ensure_ascii=True) + "\n")


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")
    tmp_path.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON while preserving the caller's expected fallback shape."""

    if not path.exists():
        return deepcopy(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)
    if default is None:
        return payload
    if isinstance(default, dict):
        return payload if isinstance(payload, dict) else dict(default)
    if isinstance(default, list):
        return payload if isinstance(payload, list) else list(default)
    return payload if isinstance(payload, type(default)) else deepcopy(default)
