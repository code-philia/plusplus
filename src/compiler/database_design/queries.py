"""Requirement-scoped schema views and database traceability."""

from __future__ import annotations

import copy
from typing import Any


def schema_for_requirement(schema: dict[str, Any], requirement_id: str) -> dict[str, Any]:
    """Query final Schema IR through its explicit requirement traceability index."""

    traceability = schema.get("traceability", {}).get("requirements", {})
    links = traceability.get(requirement_id, {}) if isinstance(traceability, dict) else {}
    entity_keys = set(links.get("entities", []))
    relationship_ids = set(links.get("relationships", []))
    constraint_ids = set(links.get("constraints", []))
    return {
        "entities": [copy.deepcopy(item) for item in schema.get("entities", []) if item.get("key") in entity_keys],
        "relationships": [
            copy.deepcopy(item) for item in schema.get("relationships", []) if item.get("id") in relationship_ids
        ],
        "constraints": [
            copy.deepcopy(item) for item in schema.get("constraints", []) if item.get("id") in constraint_ids
        ],
    }


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
            str(entity): set() for entity in links.get("entities", [])
        }
        for reference in links.get("fields", []):
            entity, separator, field_name = str(reference).partition(".")
            if separator and entity and field_name:
                entity_fields.setdefault(entity, set()).add(field_name)
        result[str(requirement_id)] = {
            entity: sorted(fields) for entity, fields in sorted(entity_fields.items())
        }
    return result
