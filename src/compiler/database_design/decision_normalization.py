"""Normalize deterministic decision omissions and repetitions before validation."""
from __future__ import annotations

import copy
from typing import Any

from .common import normalize_identifier


def normalize_decision_context(
    phase: str,
    decision: Any,
    context: dict[str, Any],
) -> tuple[Any, list[str]]:
    """Repair omissions and repetitions whose meaning is deterministic."""


    if phase != "pass2_fields" or not isinstance(decision, dict):
        return decision, []
    entities = decision.get("entities")
    related_entities = context.get("related_entities")
    if (
        not isinstance(entities, list)
        or not isinstance(related_entities, list)
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("entity"), str)
            or set(item) != {"entity", "new_fields", "update_fields"}
            or not isinstance(item.get("update_fields"), list)
            or not isinstance(item.get("new_fields"), list)
            for item in entities
        )
    ):
        return decision, []

    normalized = copy.deepcopy(decision)
    grouped: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    for item in normalized["entities"]:
        key = normalize_identifier(item["entity"])
        target = grouped.get(key)
        if target is None:
            target = {"entity": key, "new_fields": [], "update_fields": []}
            grouped[key] = target
        else:
            notes.append(f"merged_repeated_entity:{key}")
        for operation in ("new_fields", "update_fields"):
            for field_item in item[operation]:
                if field_item not in target[operation]:
                    target[operation].append(field_item)

    expected = [
        normalize_identifier(str(item.get("key", "")))
        for item in related_entities
        if isinstance(item, dict)
    ]
    ordered_keys = [key for key in expected if key in grouped] + sorted(set(grouped) - set(expected))
    normalized["entities"] = [grouped[key] for key in ordered_keys]
    return normalized, notes


# Keep the established import identity for callers and serialized references.
normalize_decision_context.__module__ = 'compiler.database_design.validation'
