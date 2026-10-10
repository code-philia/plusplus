"""Requirement-scoped schema views and database traceability."""

from __future__ import annotations

import copy
from typing import Any


def schema_for_requirement(schema: dict[str, Any], requirement_id: str) -> dict[str, Any]:
    """Query final Schema IR through its explicit requirement traceability index."""

    traceability = schema.get("traceability", {}).get("requirements", {})
    links = traceability.get(requirement_id, {}) if isinstance(traceability, dict) else {}
    table_names = set(links.get("tables", []))
    return {"tables": [copy.deepcopy(table) for table in schema.get("tables", [])
                       if table["name"] in table_names
                       or requirement_id in table.get("requirement_ids", [])]}


def database_traceability(schema: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Project provenance to requirement -> entity -> field names."""

    raw = schema.get("traceability", {}).get("requirements", {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, list[str]]] = {}
    for requirement_id, links in sorted(raw.items()):
        if not isinstance(links, dict):
            continue
        entity_fields: dict[str, set[str]] = {
            str(entity): set() for entity in links.get("tables", [])
        }
        for reference in links.get("columns", []):
            entity, separator, field_name = str(reference).partition(".")
            if separator and entity and field_name:
                entity_fields.setdefault(entity, set()).add(field_name)
        result[str(requirement_id)] = {
            entity: sorted(fields) for entity, fields in sorted(entity_fields.items())
        }
    return result
