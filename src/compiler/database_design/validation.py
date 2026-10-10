"""Validation and normalization of ER decisions and relational schemas."""

from __future__ import annotations

import copy
import math
import re
from datetime import date, datetime
from typing import Any

from .common import _normalize_identifier
from .contracts import (
    CONSTRAINT_TYPES,
    FIELD_DECLARATION_SCHEMA,
    FIELD_ORIGINS,
    FIELD_PROPERTIES_SCHEMA,
    FIELD_TYPES,
    IDENTIFIER_PATTERN,
    RELATIONSHIP_TYPES,
)


def validate_database_schema(
    schema: dict[str, Any],
    *,
    expected_requirement_ids: set[str] | None = None,
) -> list[str]:
    """Statically validate a final Stage 1 schema without modifying it."""

    if not isinstance(schema, dict):
        return ["schema must be a JSON object"]
    if schema.get("status") != "RESOLVED":
        return ["schema status must be RESOLVED"]
    entities = schema.get("entities")
    relationships = schema.get("relationships")
    constraints = schema.get("constraints")
    traceability_container = schema.get("traceability")
    traceability = traceability_container.get("requirements") if isinstance(traceability_container, dict) else None
    if not isinstance(entities, list) or not isinstance(relationships, list) or not isinstance(constraints, list):
        return ["entities, relationships, and constraints must be arrays"]
    if not isinstance(traceability, dict):
        return ["traceability.requirements must be an object"]

    errors: list[str] = []
    entity_map: dict[str, dict[str, Any]] = {}
    field_refs: set[str] = set()
    for entity in entities:
        if not isinstance(entity, dict):
            errors.append("entity must be an object")
            continue
        key = str(entity.get("key", ""))
        if not IDENTIFIER_PATTERN.fullmatch(key) or key in entity_map:
            errors.append(f"invalid or duplicate entity key: {key}")
            continue
        entity_map[key] = entity
        fields = entity.get("fields")
        if not isinstance(fields, list):
            errors.append(f"entity fields must be an array: {key}")
            continue
        names: set[str] = set()
        for field_item in fields:
            name = str(field_item.get("name", "")) if isinstance(field_item, dict) else ""
            if not IDENTIFIER_PATTERN.fullmatch(name) or name in names:
                errors.append(f"invalid or duplicate field: {key}.{name}")
                continue
            names.add(name)
            field_refs.add(f"{key}.{name}")
            if field_item.get("type") not in FIELD_TYPES:
                errors.append(f"invalid field type: {key}.{name}")
            if field_item.get("origin") not in FIELD_ORIGINS:
                errors.append(f"invalid field origin: {key}.{name}")
            if not isinstance(field_item.get("nullable"), bool):
                errors.append(f"invalid nullable flag: {key}.{name}")
            errors.extend(_field_property_errors(key, field_item))
        if "id" not in names:
            errors.append(f"entity has no id field: {key}")
        else:
            id_field = next(item for item in fields if isinstance(item, dict) and item.get("name") == "id")
            if id_field.get("type") != "uuid" or not id_field.get("primary_key") or id_field.get("origin") != "SYSTEM":
                errors.append(f"entity id is not a compiler-provided uuid primary key: {key}")
    for entity_key, entity in entity_map.items():
        for field_item in entity.get("fields", []):
            reference = field_item.get("references")
            if reference is not None and reference not in field_refs:
                errors.append(f"foreign key target does not exist: {entity_key}.{field_item.get('name')} -> {reference}")
            if reference is not None and (field_item.get("type") != "foreign_key" or field_item.get("origin") != "RELATIONSHIP"):
                errors.append(f"invalid foreign key field: {entity_key}.{field_item.get('name')}")

    relationship_ids: set[str] = set()
    for relationship in relationships:
        relationship_id = str(relationship.get("id", "")) if isinstance(relationship, dict) else ""
        if not relationship_id or relationship_id in relationship_ids:
            errors.append(f"invalid or duplicate relationship id: {relationship_id}")
            continue
        relationship_ids.add(relationship_id)
        if "key" in relationship:
            definition = {name: relationship.get(name) for name in (
                "key", "parent", "child", "type", "parent_min", "parent_max", "child_min", "child_max", "description", "evidence_requirement_ids",
            )}
            errors.extend(_validate_decision_shape("pass3_relationships", "FINAL", {
                "entity_key": "FINAL", "relationships": [definition],
            }))
            if relationship.get("child_required") != (relationship.get("child_min") == 1):
                errors.append(f"relationship required flag conflicts with ER participation: {relationship_id}")
        if relationship.get("parent") not in entity_map or relationship.get("child") not in entity_map:
            errors.append(f"relationship endpoint does not exist: {relationship_id}")
        if relationship.get("type") not in RELATIONSHIP_TYPES:
            errors.append(f"invalid relationship type: {relationship_id}")
        if relationship.get("fk_entity") is not None and f"{relationship.get('fk_entity')}.{relationship.get('fk_field')}" not in field_refs:
            errors.append(f"relationship foreign key does not exist: {relationship_id}")
        if relationship.get("type") == "MANY_TO_MANY" and relationship.get("association_entity") not in entity_map:
            errors.append(f"relationship association entity does not exist: {relationship_id}")
        if relationship.get("type") in {"ONE_TO_ONE", "ONE_TO_MANY"}:
            expected_ref = f"{relationship.get('parent')}.id"
            fk_entity = entity_map.get(str(relationship.get("fk_entity")), {})
            fk = next(
                (item for item in fk_entity.get("fields", []) if item.get("name") == relationship.get("fk_field")),
                {},
            )
            if relationship.get("fk_entity") != relationship.get("child") or fk.get("references") != expected_ref:
                errors.append(f"relationship is not lowered on the child side: {relationship_id}")
        if not relationship.get("requirement_ids"):
            errors.append(f"relationship has no requirement traceability: {relationship_id}")

    constraint_ids: set[str] = set()
    for constraint in constraints:
        constraint_id = str(constraint.get("id", "")) if isinstance(constraint, dict) else ""
        if not constraint_id or constraint_id in constraint_ids:
            errors.append(f"invalid or duplicate constraint id: {constraint_id}")
            continue
        constraint_ids.add(constraint_id)
        if constraint.get("type") not in CONSTRAINT_TYPES:
            errors.append(f"invalid constraint: {constraint_id}")
        constraint_fields = constraint.get("fields", [])
        if not isinstance(constraint_fields, list):
            errors.append(f"constraint fields must be an array: {constraint_id}")
            constraint_fields = []
        elif any(reference not in field_refs for reference in constraint_fields):
            errors.append(f"constraint references an unknown field: {constraint_id}")
        if constraint.get("type") == "UNIQUE" and len(constraint_fields) != 1:
            errors.append(f"UNIQUE constraint must reference one field: {constraint_id}")
        if constraint.get("type") == "COMPOSITE_UNIQUE" and len(constraint_fields) < 2:
            errors.append(f"COMPOSITE_UNIQUE constraint must reference multiple fields: {constraint_id}")

    if expected_requirement_ids is not None:
        if expected_requirement_ids - set(traceability):
            errors.append("missing requirement traceability: " + ", ".join(sorted(expected_requirement_ids - set(traceability))))
        if set(traceability) - expected_requirement_ids:
            errors.append("unexpected requirement traceability: " + ", ".join(sorted(set(traceability) - expected_requirement_ids)))
    for requirement_id, links in traceability.items():
        if not isinstance(links, dict):
            errors.append(f"invalid requirement traceability: {requirement_id}")
            continue
        if any(key not in entity_map for key in links.get("entities", [])):
            errors.append(f"traceability references an unknown entity: {requirement_id}")
        if any(ref not in field_refs for ref in links.get("fields", [])):
            errors.append(f"traceability references an unknown field: {requirement_id}")
        if any(value not in relationship_ids for value in links.get("relationships", [])):
            errors.append(f"traceability references an unknown relationship: {requirement_id}")
        if any(value not in constraint_ids for value in links.get("constraints", [])):
            errors.append(f"traceability references an unknown constraint: {requirement_id}")
    return errors


def _field_property_errors(entity_key: str, field_item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    name = str(field_item.get("name", ""))
    field_type = field_item.get("type")
    if not isinstance(field_type, str) or field_type not in FIELD_TYPES:
        return [f"invalid field type: {entity_key}.{name}"]
    properties = field_item.get("properties")
    if not isinstance(properties, dict):
        return [f"field properties must be an object: {entity_key}.{name}"]
    unknown = set(properties) - {
        "min_length", "max_length", "enum", "minimum", "maximum", "default", "unique", "comparison",
    }
    if unknown:
        errors.append(f"unknown field properties: {entity_key}.{name} -> {', '.join(sorted(unknown))}")
    for key in ("min_length", "max_length"):
        value = properties.get(key)
        if value is not None and (type(value) is not int or value < 0):
            errors.append(f"{key} must be a nonnegative integer: {entity_key}.{name}")
    for key in ("minimum", "maximum"):
        value = properties.get(key)
        if value is not None and (
            type(value) not in {int, float} or not math.isfinite(value)
            or (field_type == "integer" and int(value) != value)
        ):
            errors.append(f"invalid numeric {key}: {entity_key}.{name}")
    if properties.get("unique") is not None and type(properties["unique"]) is not bool:
        errors.append(f"unique must be a boolean: {entity_key}.{name}")
    if properties.get("comparison") not in (None, "exact", "case_insensitive"):
        errors.append(f"invalid comparison: {entity_key}.{name}")
    if properties.get("comparison") is not None and field_type != "string":
        errors.append(f"comparison requires a string field: {entity_key}.{name}")
    if properties.get("comparison") is not None and properties.get("unique") is not True:
        errors.append(f"comparison requires unique=true: {entity_key}.{name}")
    enum = properties.get("enum")
    if enum is not None and (
        not isinstance(enum, list) or not enum
        or any(not isinstance(value, str) for value in enum)
        or any(isinstance(value, str) and "\x00" in value for value in enum)
        or len(enum) != len(set(value for value in enum if isinstance(value, str)))
    ):
        errors.append(f"enum must be a nonempty list of distinct strings: {entity_key}.{name}")
    default = properties.get("default")
    if default is not None:
        valid_default = (
            (field_type in {"string", "date", "datetime", "uuid", "foreign_key"} and isinstance(default, str))
            or (field_type == "integer" and type(default) is int)
            or (field_type == "number" and type(default) in {int, float} and math.isfinite(default))
            or (field_type == "boolean" and isinstance(default, bool))
            or (field_type == "json" and type(default) in {str, int, float, bool})
        )
        if not valid_default:
            errors.append(f"default does not match field type: {entity_key}.{name}")
    # Malformed values must produce diagnostics rather than comparison errors.
    if errors:
        return errors
    active = {key for key, value in properties.items() if value is not None and value is not False}
    if active & {"min_length", "max_length"} and field_type != "string":
        errors.append(f"string property used on non-string field: {entity_key}.{name}")
    if active & {"minimum", "maximum"} and field_type not in {"integer", "number"}:
        errors.append(f"numeric property used on non-numeric field: {entity_key}.{name}")
    if "enum" in active and field_type != "string":
        errors.append(f"enum property used on non-string field: {entity_key}.{name}")
    if properties.get("min_length") is not None and properties.get("max_length") is not None:
        if properties["min_length"] > properties["max_length"]:
            errors.append(f"invalid length range: {entity_key}.{name}")
    if properties.get("minimum") is not None and properties.get("maximum") is not None:
        if properties["minimum"] > properties["maximum"]:
            errors.append(f"invalid numeric range: {entity_key}.{name}")
    if isinstance(default, str):
        if properties.get("min_length") is not None and len(default) < properties["min_length"]:
            errors.append(f"default is shorter than min_length: {entity_key}.{name}")
        if properties.get("max_length") is not None and len(default) > properties["max_length"]:
            errors.append(f"default is longer than max_length: {entity_key}.{name}")
        if enum is not None and default not in enum:
            errors.append(f"default is outside enum: {entity_key}.{name}")
    if type(default) in {int, float}:
        if properties.get("minimum") is not None and default < properties["minimum"]:
            errors.append(f"default is below minimum: {entity_key}.{name}")
        if properties.get("maximum") is not None and default > properties["maximum"]:
            errors.append(f"default is above maximum: {entity_key}.{name}")
    if default is not None and not logical_literal_valid(field_type, default):
        errors.append(f"default is not a valid logical value: {entity_key}.{name}")
    return errors


def logical_literal_valid(kind: str, value: Any) -> bool:
    """Fixed logical value contracts also used for schema defaults."""
    if kind != "json" and isinstance(value, str) and "\x00" in value:
        return False
    if type(value) in {int, float} and not math.isfinite(value):
        return False
    if kind == "uuid":
        return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value) is not None
    if kind not in {"date", "datetime"}:
        return True
    try:
        if kind == "date":
            return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})", value
        ):
            return False
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _validate_decision_shape(phase: str, node_id: str, decision: Any) -> list[str]:
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
                        for error in _field_property_errors("decision", field_item)
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


def _validate_decision_context(
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
            _normalize_identifier(str(item.get("key", "")))
            for item in context.get("existing_entities", [])
            if isinstance(item, dict)
        }
        reused = {
            _normalize_identifier(str(value))
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
        _normalize_identifier(str(item.get("key", ""))): item
        for item in related_entities or []
        if isinstance(item, dict)
    }
    related = set(related_by_key)

    if phase == "pass2_fields":
        entities = decision.get("entities")
        if not isinstance(entities, list):
            return []
        actual = {
            _normalize_identifier(str(item.get("entity", "")))
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
            key = _normalize_identifier(str(item.get("entity", "")))
            known_fields = {
                _normalize_identifier(str(field_item.get("name", ""))): field_item
                for field_item in related_by_key.get(key, {}).get("fields", [])
                if isinstance(field_item, dict)
            }
            names_by_operation: dict[str, set[str]] = {}
            for operation in ("new_fields", "update_fields"):
                declarations = item.get(operation, [])
                if not isinstance(declarations, list):
                    continue
                names = [
                    _normalize_identifier(str(field_item.get("name", "")))
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
            relation_key = _normalize_identifier(str(item.get("key", "")))
            parent = _normalize_identifier(str(item.get("parent", "")))
            child = _normalize_identifier(str(item.get("child", "")))
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


def _normalize_decision_context(
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
        key = _normalize_identifier(item["entity"])
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
        _normalize_identifier(str(item.get("key", "")))
        for item in related_entities
        if isinstance(item, dict)
    ]
    ordered_keys = [key for key in expected if key in grouped] + sorted(set(grouped) - set(expected))
    normalized["entities"] = [grouped[key] for key in ordered_keys]
    return normalized, notes
