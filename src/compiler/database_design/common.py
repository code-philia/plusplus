"""Internal helpers shared by database design components."""

from __future__ import annotations

import copy
import re
from typing import Any


def _compact_properties(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: copy.deepcopy(value)
        for key, value in raw.items()
        if value is not None
    }


def _merge_properties(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    entity: str,
    field_name: str,
    requirement_id: str,
    errors: list[str],
) -> None:
    for key, value in _compact_properties(incoming).items():
        if key in existing and existing[key] != value:
            errors.append(_format_error("ARC2228", f"Conflicting field property: {entity}.{field_name}.{key}.", node_id=requirement_id))
        else:
            existing[key] = copy.deepcopy(value)


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _relationship_id(key: str) -> str:
    return f"relationship_{key}"


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
        values.sort()


def _format_error(code: str, message: str, *, node_id: str | None = None) -> str:
    return f"{code}: {message}" + (f" ({node_id})" if node_id else "")
