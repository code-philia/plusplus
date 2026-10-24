"""Validate model decision shapes and the context slices supplied to each ER pass."""
from __future__ import annotations

from typing import Any

from .common import normalize_identifier
from .contracts import (
    FIELD_DECLARATION_SCHEMA,
    FIELD_PROPERTIES_SCHEMA,
    FIELD_TYPES,
    IDENTIFIER_PATTERN,
    RELATIONSHIP_TYPES,
)
from .field_validation import field_property_errors


def validate_decision_shape(phase: str, node_id: str, decision: Any) -> list[str]:
    if not isinstance(decision, dict):
        return ["decision must be an object"]
    errors: list[str] = []
    target_key = "entity_key" if phase == "pass3_relationships" else "requirement_id"
    if decision.get(target_key) != node_id:
        errors.append(f"{target_key} does not match target")
    required_arrays = {
        "pass1_entities": ("reuse_entities", "new_entities"),
        "pass2_fields": ("entities",),
        "pass3_relationships": ("relationships",),
    }[phase]
    errors.extend(f"{key} must be an array" for key in required_arrays if not isinstance(decision.get(key), list))
    if errors:
        return errors
    if phase == "pass1_entities":
        if any(not isinstance(value, str) for value in decision["reuse_entities"]):
            errors.append("Pass 1 reuse_entities contains a non-string value")
        for item in decision["new_entities"]:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not isinstance(item.get("description"), str):
                errors.append("Pass 1 new_entities contains an invalid entity")
    elif phase == "pass2_fields":
        for item in decision["entities"]:
            if not isinstance(item, dict) or not isinstance(item.get("entity"), str):
                errors.append("Pass 2 contains an invalid entity decision")
                continue
            if set(item) != {"entity", "new_fields", "update_fields"}:
                errors.append("Pass 2 entity must contain only entity, new_fields and update_fields")
            if not isinstance(item.get("new_fields"), list) or not isinstance(item.get("update_fields"), list):
                errors.append("Pass 2 field lists must be arrays")
                continue
            if not item["new_fields"] and not item["update_fields"]:
                errors.append("Pass 2 must omit entities with no field changes")
            for operation in ("new_fields", "update_fields"):
                for field_item in item[operation]:
                    if not isinstance(field_item, dict):
                        errors.append(f"Pass 2 {operation} contains a non-object")
                        continue
                    if set(field_item) != set(FIELD_DECLARATION_SCHEMA["required"]):
                        errors.append(f"Pass 2 {operation} must contain a complete field declaration")
                    if not isinstance(field_item.get("type"), str) or field_item["type"] not in FIELD_TYPES - {"uuid", "foreign_key"}:
                        errors.append(f"Pass 2 {operation} field has an invalid type")
                    name = field_item.get("name")
                    if not isinstance(name, str) or not IDENTIFIER_PATTERN.fullmatch(name):
                        errors.append(f"Pass 2 {operation} field has an invalid name")
                    if not isinstance(field_item.get("nullable"), bool):
                        errors.append(f"Pass 2 {operation} field has an invalid nullable flag")
                    if not isinstance(field_item.get("description"), str):
                        errors.append(f"Pass 2 {operation} field has an invalid description")
                    properties = field_item.get("properties")
                    if not isinstance(properties, dict):
                        errors.append(f"Pass 2 {operation} field properties must be an object")
                        continue
                    if set(properties) != set(FIELD_PROPERTIES_SCHEMA["required"]):
                        errors.append(f"Pass 2 {operation} field properties must contain all eight storage-invariant keys")
                    errors.extend(
                        f"Pass 2 invalid field properties: {error}"
                        for error in field_property_errors("decision", field_item)
                    )
    elif phase == "pass3_relationships":
        for item in decision["relationships"]:
            if not isinstance(item, dict):
                errors.append("Pass 3 relationships contains a non-object")
                continue
            required = {"key", "parent", "child", "type", "parent_min", "parent_max", "child_min", "child_max", "description", "evidence_requirement_ids"}
            if set(item) != required:
                errors.append("Pass 3 relationship must contain the complete ER relationship definition")
            if not isinstance(item.get("type"), str) or item["type"] not in RELATIONSHIP_TYPES:
                errors.append("Pass 3 relationship has invalid type")
            if any(type(item.get(name)) is not int or item[name] not in (0, 1) for name in ("parent_min", "child_min")):
                errors.append("Pass 3 relationship minimum cardinalities must be 0 or 1")
            if any(not isinstance(item.get(name), str) or item[name] not in ("one", "many") for name in ("parent_max", "child_max")):
                errors.append("Pass 3 relationship maximum cardinalities must be one or many")
            expected_type = {
                ("one", "one"): "ONE_TO_ONE",
                ("many", "one"): "ONE_TO_MANY",
                ("many", "many"): "MANY_TO_MANY",
            }.get((str(item.get("parent_max")), str(item.get("child_max"))))
            if expected_type is None or item.get("type") != expected_type:
                errors.append("Pass 3 relationship type conflicts with endpoint cardinalities")
            if not isinstance(item.get("key"), str) or not IDENTIFIER_PATTERN.fullmatch(str(item.get("key", ""))):
                errors.append("Pass 3 relationship key is invalid")
            if not isinstance(item.get("parent"), str) or not isinstance(item.get("child"), str):
                errors.append("Pass 3 relationship has invalid endpoints")
            if not isinstance(item.get("description"), str) or not item.get("description", "").strip():
                errors.append("Pass 3 relationship description must explain its domain meaning")
            evidence = item.get("evidence_requirement_ids")
            if not isinstance(evidence, list) or not evidence or any(not isinstance(rid, str) for rid in evidence):
                errors.append("Pass 3 relationship evidence must be a nonempty list of requirement IDs")
    return errors

def validate_decision_context(
    phase: str,
    decision: Any,
    context: dict[str, Any],
) -> list[str]:
    """Reject structurally valid decisions that contradict their supplied slice."""

    if not isinstance(decision, dict):
        return []
    errors: list[str] = []
    if phase == "pass1_entities":
        existing = {
            normalize_identifier(str(item.get("key", "")))
            for item in context.get("existing_entities", [])
            if isinstance(item, dict)
        }
        reused = {
            normalize_identifier(str(value))
            for value in decision.get("reuse_entities", [])
            if isinstance(value, str)
        }
        if reused - existing:
            errors.append("Pass 1 reuses unknown entities: " + ", ".join(sorted(reused - existing)))
        return errors

    related_entities = context.get("related_entities")
    if phase == "pass2_fields" and not isinstance(related_entities, list):
        return []
    related_by_key = {
        normalize_identifier(str(item.get("key", ""))): item
        for item in related_entities or []
        if isinstance(item, dict)
    }
    related = set(related_by_key)

    if phase == "pass2_fields":
        entities = decision.get("entities")
        if not isinstance(entities, list):
            return []
        actual = {
            normalize_identifier(str(item.get("entity", "")))
            for item in entities
            if isinstance(item, dict)
        }
        if actual - related:
            errors.append(
                "Pass 2 referenced entities outside its supplied slice: "
                + ", ".join(sorted(actual - related))
            )
        for item in entities:
            if not isinstance(item, dict):
                continue
            key = normalize_identifier(str(item.get("entity", "")))
            known_fields = {
                normalize_identifier(str(field_item.get("name", ""))): field_item
                for field_item in related_by_key.get(key, {}).get("fields", [])
                if isinstance(field_item, dict)
            }
            names_by_operation: dict[str, set[str]] = {}
            for operation in ("new_fields", "update_fields"):
                declarations = item.get(operation, [])
                if not isinstance(declarations, list):
                    continue
                names = [
                    normalize_identifier(str(field_item.get("name", "")))
                    for field_item in declarations if isinstance(field_item, dict)
                ]
                names_by_operation[operation] = set(names)
                if len(names) != len(set(names)):
                    errors.append(f"Pass 2 {operation} repeats a field on {key}")
                forbidden = {
                    name for name in names
                    if name == "id" or name.endswith("_id")
                    or known_fields.get(name, {}).get("origin") in {"SYSTEM", "RELATIONSHIP"}
                }
                if forbidden:
                    errors.append(
                        f"Pass 2 cannot create or update system or foreign-key fields on {key}: "
                        + ", ".join(sorted(forbidden))
                    )
            new_names = names_by_operation.get("new_fields", set())
            updated_names = names_by_operation.get("update_fields", set())
            if new_names & known_fields.keys():
                errors.append(
                    f"Pass 2 redeclares existing fields on {key}; use update_fields: "
                    + ", ".join(sorted(new_names & known_fields.keys()))
                )
            if updated_names - known_fields.keys():
                errors.append(
                    f"Pass 2 updates unknown fields on {key}: "
                    + ", ".join(sorted(updated_names - known_fields.keys()))
                )
            if new_names & updated_names:
                errors.append(f"Pass 2 puts the same field in new_fields and update_fields on {key}")
        return errors

    if phase == "pass3_relationships":
        graph = context.get("er_graph", {})
        allowed = {item["key"] for item in graph.get("entities", []) if isinstance(item, dict)}
        focus_key = context.get("focus_entity", {}).get("key")
        evidence_ids = {item["id"] for item in context.get("requirement_catalog", []) if isinstance(item, dict)}
        seen: dict[str, dict[str, Any]] = {}
        for item in decision.get("relationships", []):
            if not isinstance(item, dict):
                continue
            relation_key = normalize_identifier(str(item.get("key", "")))
            parent = normalize_identifier(str(item.get("parent", "")))
            child = normalize_identifier(str(item.get("child", "")))
            if parent not in allowed or child not in allowed:
                errors.append(f"Pass 3 references entities outside its supplied slice: {parent} -> {child}")
                continue
            evidence = item.get("evidence_requirement_ids")
            if isinstance(evidence, list) and all(isinstance(rid, str) for rid in evidence):
                if set(evidence) - evidence_ids:
                    errors.append("Pass 3 evidence must reference supplied requirements")
            if focus_key not in (parent, child):
                errors.append("Pass 3 relationship must involve the focus entity")
            if relation_key in seen and seen[relation_key] != item:
                errors.append(f"Pass 3 emits conflicting definitions for relationship: {relation_key}")
            seen[relation_key] = item
        for existing in graph.get("relationships", []):
            if not isinstance(existing, dict):
                continue
            key = str(existing.get("key", ""))
            if key in seen:
                item = seen[key]
                for name in ("parent", "child", "type", "parent_max", "child_max"):
                    if str(item.get(name)) != str(existing.get(name)):
                        errors.append(f"Pass 3 conflicts with an existing relationship: {key}")
                        break
        return errors

    return errors


# Keep the established import identity for callers and serialized references.
validate_decision_shape.__module__ = 'compiler.database_design.validation'
validate_decision_context.__module__ = 'compiler.database_design.validation'
