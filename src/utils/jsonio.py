from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")
    tmp_path.replace(path)


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict):
        raise ValueError(f'Expected a JSON object: {path}')
    return payload


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON while preserving the caller's expected fallback shape."""

    if not path.exists():
        return deepcopy(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)
    if default is None:
        return payload
    if isinstance(default, dict):
        return payload if isinstance(payload, dict) else dict(default)
    if isinstance(default, list):
        return payload if isinstance(payload, list) else list(default)
    return payload if isinstance(payload, type(default)) else deepcopy(default)
