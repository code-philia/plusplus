"""Compile requirement seed declarations into validated, compiler-owned Fixture IR."""

from __future__ import annotations

import copy
import hashlib
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.logging import SynchronousLog

from .database_stage import schema_for_requirement
from .model_client import StructuredModel, describe_model_error


FIXTURE_IR_SCHEMA_VERSION = 1
FIXTURE_STATUS = "FIXTURES_FROZEN"

FIXTURE_VALUE_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "string"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["fixture_key", "field"],
            "properties": {
                "fixture_key": {"type": "string"},
                "field": {"type": "string"},
            },
        },
    ]
}

FIXTURE_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fixture_sets"],
    "properties": {
        "fixture_sets": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "rows"],
                "properties": {
                    "name": {"type": "string"},
                    "rows": {
                        "type": "array",
                        "maxItems": 64,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["entity_key", "fixture_key", "values"],
                            "properties": {
                                "entity_key": {"type": "string"},
                                "fixture_key": {"type": "string"},
                                "values": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["field", "value"],
                                        "properties": {
                                            "field": {"type": "string"},
                                            "value": FIXTURE_VALUE_SCHEMA,
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
    },
}

FIXTURE_INSTRUCTIONS = """You compile declared test starting state into Fixture IR for one atomic requirement.
Use only the supplied seed declarations and local Database Schema slice. Return semantic entity keys and field names
exactly as supplied. Include every non-nullable field that has no default, except primary keys: the compiler owns
primary keys, row ids, insert order, and timestamps/defaults. Use fixture_key to name a row. A field may reference a
previous row with {"fixture_key":"...","field":"id"}. Do not emit SQL, TypeScript, table names, column names,
routes, implementation behavior, or undeclared example records. Return exactly one JSON object and no prose.
If none of the declared records can be mapped to an entity in the supplied local schema, return
{"fixture_sets":[]} so the compiler can continue without inventing database entities.
"""


@dataclass(slots=True)
class FixturePassResult:
    fixture_ir: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class FixturePass:
    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self._model = model
        self._retries = _bounded_int("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", 2, 0, 10)
        self._log = SynchronousLog("FixturePass", workspace_root=artifact_root.resolve().parent)

    def compile(
        self,
        requirement_ir: dict[str, Any],
        database_schema: dict[str, Any],
    ) -> FixturePassResult:
        sets: list[dict[str, Any]] = []
        errors: list[str] = []
        warnings: list[str] = []
        nodes = requirement_ir.get("nodes", {})
        for requirement_id in requirement_ir.get("atomic_units", []):
            node = nodes.get(requirement_id, {}) if isinstance(nodes, dict) else {}
            declarations = node.get("seed_fixtures", []) if isinstance(node, dict) else []
            if not declarations:
                continue
            local_schema = schema_for_requirement(database_schema, str(requirement_id))
            explicit = _explicit_decision(declarations)
            decision = explicit or self._decide(
                str(requirement_id),
                node,
                declarations,
                local_schema,
                errors,
            )
            if decision is None:
                continue
            compiled, local_errors, local_warnings = _compile_decision(
                str(requirement_id), decision, local_schema
            )
            sets.extend(compiled)
            errors.extend(local_errors)
            warnings.extend(local_warnings)
        fixture_ir = {
            "schema_version": FIXTURE_IR_SCHEMA_VERSION,
            "status": FIXTURE_STATUS if not errors else "FIXTURE_FAILED",
            "fixture_sets": sets,
        }
        return FixturePassResult(fixture_ir, list(dict.fromkeys(errors)), list(dict.fromkeys(warnings)))

    def _decide(
        self,
        requirement_id: str,
        node: dict[str, Any],
        declarations: list[dict[str, Any]],
        local_schema: dict[str, Any],
        errors: list[str],
    ) -> dict[str, Any] | None:
        feedback: list[str] = []
        for attempt in range(self._retries + 1):
            payload = {
                "requirement": {
                    key: copy.deepcopy(node.get(key))
                    for key in ("id", "name", "description", "scenarios")
                },
                "seed_declarations": copy.deepcopy(declarations),
                "database_schema": copy.deepcopy(local_schema),
            }
            if feedback:
                payload["validation_feedback"] = feedback
            self._log.info(
                f"MODEL_REQUEST requirement={requirement_id} attempt={attempt + 1}/{self._retries + 1}"
            )
            try:
                decision = self._model.generate_json(
                    schema_name="arc_fixture_ir",
                    instructions=FIXTURE_INSTRUCTIONS,
                    input_payload=payload,
                    output_schema=FIXTURE_DECISION_SCHEMA,
                )
            except Exception as exc:
                feedback = [describe_model_error(exc)]
                continue
            if isinstance(decision.get("fixture_sets"), list):
                return decision
            feedback = [
                "fixture_sets must be an array; use [] when no declared entity matches the local schema."
            ]
        errors.append(
            f"ARC2402 FIXTURE_INVALID: {requirement_id}: "
            + (feedback[-1] if feedback else "no valid fixture output")
        )
        return None


def attach_fixture_sets(requirement_ir: dict[str, Any], fixture_ir: dict[str, Any]) -> None:
    by_requirement: dict[str, list[dict[str, Any]]] = {}
    for row in fixture_ir.get("fixture_sets", []):
        if isinstance(row, dict):
            by_requirement.setdefault(str(row.get("requirement_id", "")), []).append(copy.deepcopy(row))
    nodes = requirement_ir.get("nodes", {})
    if not isinstance(nodes, dict):
        return
    requirement_ir["seed_fixtures"] = [
        copy.deepcopy(row)
        for row in fixture_ir.get("fixture_sets", [])
        if isinstance(row, dict)
    ]
    for requirement_id, node in nodes.items():
        if isinstance(node, dict):
            node["seed_fixtures"] = by_requirement.get(str(requirement_id), [])


def validate_fixture_ir(
    fixture_ir: dict[str, Any],
    database_schema: dict[str, Any],
    *,
    expected_requirement_ids: set[str],
) -> list[str]:
    if fixture_ir.get("status") != FIXTURE_STATUS:
        return ["Fixture IR status is not FIXTURES_FROZEN."]
    errors: list[str] = []
    seen_ids: set[str] = set()
    for fixture_set in fixture_ir.get("fixture_sets", []):
        if not isinstance(fixture_set, dict):
            errors.append("Fixture set must be an object.")
            continue
        set_id = str(fixture_set.get("id", ""))
        requirement_id = str(fixture_set.get("requirement_id", ""))
        if not set_id or set_id in seen_ids:
            errors.append(f"Invalid or duplicate fixture set id: {set_id!r}.")
        seen_ids.add(set_id)
        if requirement_id not in expected_requirement_ids:
            errors.append(f"Unknown fixture requirement: {requirement_id!r}.")
        _, row_errors, _ = _compile_decision(
            requirement_id,
            {"fixture_sets": [{"name": fixture_set.get("name", "seed"), "rows": fixture_set.get("rows", [])}]},
            schema_for_requirement(database_schema, requirement_id),
            preserve_compiler_rows=True,
        )
        errors.extend(row_errors)
    return list(dict.fromkeys(errors))


def _compile_decision(
    requirement_id: str,
    decision: dict[str, Any],
    local_schema: dict[str, Any],
    *,
    preserve_compiler_rows: bool = False,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    entities = {
        str(entity.get("key", "")): entity
        for entity in local_schema.get("entities", [])
        if isinstance(entity, dict)
    }
    compiled: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    known_keys: set[str] = set()
    known_rows: dict[str, dict[str, Any]] = {}
    for set_index, raw_set in enumerate(decision.get("fixture_sets", []), start=1):
        if not isinstance(raw_set, dict):
            errors.append(f"ARC2402 FIXTURE_INVALID: {requirement_id}: fixture set must be an object.")
            continue
        rows: list[dict[str, Any]] = []
        for row_index, raw_row in enumerate(raw_set.get("rows", []), start=1):
            if not isinstance(raw_row, dict):
                errors.append(f"ARC2402 FIXTURE_INVALID: {requirement_id}: row must be an object.")
                continue
            entity_key = str(raw_row.get("entity_key", ""))
            entity = entities.get(entity_key)
            if entity is None:
                warnings.append(
                    f"ARC2401 FIXTURE_ENTITY_DROPPED: {requirement_id}: {entity_key!r} is not in the local schema; row skipped."
                )
                continue
            fixture_key = str(raw_row.get("fixture_key", "")).strip() or f"row_{set_index}_{row_index}"
            if fixture_key in known_keys:
                warnings.append(
                    f"ARC2401 FIXTURE_KEY_DROPPED: {requirement_id}: duplicate {fixture_key!r}; row skipped."
                )
                continue
            fields = {
                str(field.get("name", "")): field
                for field in entity.get("fields", [])
                if isinstance(field, dict)
            }
            values = _row_values(raw_row.get("values"))
            for unknown in sorted(set(values) - set(fields)):
                warnings.append(
                    f"ARC2401 FIXTURE_FIELD_DROPPED: {requirement_id}: {entity_key}.{unknown}."
                )
                values.pop(unknown, None)
            for field_name, value in list(values.items()):
                field = fields[field_name]
                if isinstance(value, dict) and set(value) == {"fixture_key", "field"}:
                    target_key = str(value["fixture_key"])
                    target_field = str(value["field"])
                    target_values = known_rows.get(target_key)
                    if target_values is None or target_field not in target_values:
                        warnings.append(
                            f"ARC2401 FIXTURE_REFERENCE_DROPPED: {requirement_id}: "
                            f"{fixture_key}.{field_name} -> {target_key}.{target_field}."
                        )
                        values.pop(field_name, None)
                    else:
                        values[field_name] = copy.deepcopy(target_values[target_field])
                elif not _value_matches(value, str(field.get("type", "")), bool(field.get("nullable"))):
                    warnings.append(
                        f"ARC2401 FIXTURE_VALUE_REPLACED: {requirement_id}: "
                        f"{entity_key}.{field_name} expects {field.get('type')}; deterministic fallback used."
                    )
                    values[field_name] = _fallback_fixture_value(
                        requirement_id, fixture_key, field_name, field
                    )
            for field_name, field in fields.items():
                if field_name in values or bool(field.get("nullable")) or _has_default(field):
                    continue
                if _is_primary_key(field):
                    values[field_name] = _stable_primary_key(
                        requirement_id, fixture_key, field_name, str(field.get("type", "")), row_index
                    )
                    continue
                values[field_name] = _fallback_fixture_value(
                    requirement_id, fixture_key, field_name, field
                )
                warnings.append(
                    f"ARC2401 FIXTURE_REQUIRED_FIELD_AUTOFILLED: {requirement_id}: "
                    f"{entity_key}.{field_name}; deterministic fallback used."
                )
            known_keys.add(fixture_key)
            known_rows[fixture_key] = copy.deepcopy(values)
            row_id = str(raw_row.get("id", "")) if preserve_compiler_rows else ""
            row_id = row_id or _stable_id("ROW", requirement_id, fixture_key)
            rows.append(
                {
                    "id": row_id,
                    "entity_key": entity_key,
                    "fixture_key": fixture_key,
                    "insert_order": len(rows) + 1,
                    "values": values,
                }
            )
        name = str(raw_set.get("name", "seed")).strip() or "seed"
        set_id = str(raw_set.get("id", "")) if preserve_compiler_rows else ""
        compiled.append(
            {
                "id": set_id or _stable_id("FIXTURE_SET", requirement_id, f"{set_index}:{name}"),
                "requirement_id": requirement_id,
                "name": name,
                "rows": rows,
            }
        )
    return compiled, errors, warnings


def _explicit_decision(declarations: list[dict[str, Any]]) -> dict[str, Any] | None:
    records = [
        record
        for declaration in declarations
        if isinstance(declaration, dict)
        for record in declaration.get("records", [])
        if isinstance(record, dict)
    ]
    if not records:
        return None
    return {
        "fixture_sets": [
            {
                "name": "seed",
                "rows": [
                    {
                        "entity_key": str(record.get("entity", "")),
                        "fixture_key": f"record_{index}",
                        "values": [
                            {"field": str(key), "value": value}
                            for key, value in record.get("values", {}).items()
                        ],
                    }
                    for index, record in enumerate(records, start=1)
                ],
            }
        ]
    }


def _row_values(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): copy.deepcopy(item) for key, item in value.items()}
    if not isinstance(value, list):
        return {}
    return {
        str(item.get("field", "")): copy.deepcopy(item.get("value"))
        for item in value
        if isinstance(item, dict) and str(item.get("field", ""))
    }


def _value_matches(value: Any, field_type: str, nullable: bool) -> bool:
    if value is None:
        return nullable
    if field_type in {"string", "date", "datetime", "uuid"}:
        return isinstance(value, str)
    if field_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "json":
        return isinstance(value, (dict, list, str, int, float, bool, type(None)))
    return False


def _has_default(field: dict[str, Any]) -> bool:
    properties = field.get("properties", {})
    return isinstance(properties, dict) and "default" in properties


def _is_primary_key(field: dict[str, Any]) -> bool:
    if field.get("primary_key") is True:
        return True
    return any(
        isinstance(row, dict) and row.get("type") == "PRIMARY_KEY"
        for row in field.get("constraints", [])
    )


def _stable_primary_key(
    requirement_id: str,
    fixture_key: str,
    field_name: str,
    field_type: str,
    ordinal: int,
) -> Any:
    seed = f"arc:{requirement_id}:{fixture_key}:{field_name}"
    if field_type == "integer":
        return ordinal
    if field_type == "uuid":
        return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _stable_id(prefix: str, requirement_id: str, value: str) -> str:
    digest = hashlib.sha256(f"{requirement_id}\0{value}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}.{digest}"


def _fallback_fixture_value(
    requirement_id: str,
    fixture_key: str,
    field_name: str,
    field: dict[str, Any],
) -> Any:
    """Keep a malformed seed usable without inventing a random value."""

    field_type = str(field.get("type", "string"))
    seed = f"arc-fixture:{requirement_id}:{fixture_key}:{field_name}"
    if field_type == "integer":
        return 1
    if field_type == "number":
        return 1
    if field_type == "boolean":
        return False
    if field_type == "datetime":
        return "1970-01-01T00:00:00.000Z"
    if field_type == "date":
        return "1970-01-01"
    if field_type == "uuid":
        return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
    if field_type == "json":
        return {}
    return f"fixture_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10]}"


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))
