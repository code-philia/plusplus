"""Internal helpers shared by database design components."""

from __future__ import annotations

import copy
import re
from typing import Any


def compact_properties(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: copy.deepcopy(value)
        for key, value in raw.items()
        if value is not None
    }


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def make_relationship_id(key: str) -> str:
    return f"relationship_{key}"


def append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
        values.sort()


def format_error(code: str, message: str, *, node_id: str | None = None) -> str:
    return f"{code}: {message}" + (f" ({node_id})" if node_id else "")
