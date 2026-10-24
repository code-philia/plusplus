"""Validate final entity, relationship, constraint and traceability structures."""
from __future__ import annotations

from typing import Any

from .contracts import (
    CONSTRAINT_TYPES,
    FIELD_ORIGINS,
    FIELD_TYPES,
    IDENTIFIER_PATTERN,
    RELATIONSHIP_TYPES,
)
from .decision_validation import _validate_decision_shape
from .field_validation import _field_property_errors


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


# Keep the established import identity for callers and serialized references.
validate_database_schema.__module__ = 'compiler.database_design.validation'
