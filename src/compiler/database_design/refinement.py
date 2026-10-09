"""Deterministically refine a completed ER model into a database-neutral relational Schema IR.

The existing relationship mapper supplies the relational projection. This
pass resolves tables, scalar types and structured constraints; storage is a lowering decision.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..ir import ERModelIR, SchemaIR
from .state import DatabaseSchemaState
from .validation import validate_database_schema


@dataclass(slots=True)
class SchemaRefinementResult:
    schema: SchemaIR
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def refine_er_model(
    er_model: ERModelIR, *, requirement_ids: set[str] | None = None,
) -> SchemaRefinementResult:
    """Rebuild the relational mapping from ER alone; do not mutate the source.

    Reuse the existing structural mapper so the current SQLite consumers and
    the relational schema agree on table/column identities.
    """
    state = DatabaseSchemaState()
    errors: list[str] = []
    for rid in sorted(requirement_ids or set()):
        state.ensure_requirement(rid)
    for entity in er_model["entities"]:
        state.entities[entity["key"]] = {
            **copy.deepcopy(entity),
            "fields": {column["name"]: copy.deepcopy(column) for column in entity["fields"]},
        }
        for rid in entity["requirement_ids"]:
            state._link_entity(rid, entity["key"])
        for column in entity["fields"]:
            for rid in column["requirement_ids"]:
                if column["origin"] == "REQUIREMENT":
                    state._link_field(rid, entity["key"], column["name"])
    for relationship in er_model["relationships"]:
        for rid in relationship["requirement_ids"]:
            state.apply_relationships(rid, {"relationships": [relationship]}, errors)
    state.lower_relationships(errors)
    state.add_static_constraints()
    relational = state.to_schema(status="RESOLVED")
    errors.extend(validate_database_schema(relational, expected_requirement_ids=requirement_ids))
    relational["status"] = "PROPOSED" if errors or er_model["status"] != "RESOLVED" else "RESOLVED"
    result = refine_schema(er_model, relational)
    result.errors[:0] = errors
    return result


def refine_schema(er_model: ERModelIR, relational: dict[str, Any]) -> SchemaRefinementResult:
    """Resolve declared ER semantics without choosing a database dialect."""
    errors: list[str] = []
    warnings: list[str] = []
    tables: list[dict[str, Any]] = []
    runtime_rules: list[dict[str, Any]] = []
    fields = {
        f"{entity['key']}.{column['name']}": column
        for entity in relational["entities"] for column in entity["fields"]
    }
    mapped_types: dict[str, str] = {}
    def resolve_type(reference: str, active: set[str]) -> str:
        if reference in mapped_types:
            return mapped_types[reference]
        if reference not in fields or reference in active:
            raise ValueError(f"Unresolvable field type: {reference}")
        column = fields[reference]
        kind = column["type"]
        if kind == "foreign_key":
            kind = resolve_type(column.get("references"), active | {reference})
        mapped_types[reference] = kind
        return kind
    for reference in fields:
        try:
            resolve_type(reference, set())
        except ValueError as exc:
            errors.append(f"SCHEMA_IR_REFERENCE_INVALID: {exc}")

    for entity in sorted(relational["entities"], key=lambda item: item["key"]):
        table_name = entity["key"]
        _identifier(table_name, errors)
        columns: list[dict[str, Any]] = []
        constraints: list[dict[str, Any]] = []
        for column in sorted(entity["fields"], key=lambda item: item["name"]):
            name = column["name"]
            _identifier(name, errors)
            reference = f"{table_name}.{name}"
            resolved_type = mapped_types.get(reference)
            if resolved_type is None:
                continue
            props = column.get("properties", {})
            default = props.get("default")
            columns.append({
                "name": name, "type": resolved_type, "nullable": column["nullable"],
                "default": None if default is None else {
                    "kind": "json_literal" if resolved_type == "json" else "literal", "value": default,
                },
                "generation": "application_uuid" if column["primary_key"] and resolved_type == "uuid" else "none",
                "description": column["description"], "origin": column["origin"],
                "properties": copy.deepcopy(props), "requirement_ids": list(column["requirement_ids"]),
            })
            for kind, value in sorted(props.items()):
                if value is None or (kind in {"date_past", "date_future"} and value is False) or kind == "default":
                    continue
                if kind in {"min_length", "max_length", "minimum", "maximum", "enum"}:
                    op = {"min_length": "length_gte", "max_length": "length_lte",
                          "minimum": "gte", "maximum": "lte", "enum": "in"}[kind]
                    constraints.append(_check(table_name, name, op, value, column["requirement_ids"]))
                else:
                    runtime_rules.append({
                        "scope": reference, "kind": kind, "value": copy.deepcopy(value),
                        "reason": "Python regex/semantic format/relative time is not a portable relational CHECK",
                        "requirement_ids": list(column["requirement_ids"]),
                    })

        for constraint in relational["constraints"]:
            refs = constraint["fields"]
            if not refs or refs[0].partition(".")[0] != table_name or constraint["type"] == "NOT_NULL":
                continue
            kind = constraint["type"]
            local_columns = [ref.partition(".")[2] for ref in refs]
            table_constraint = {
                "name": _constraint_name(table_name, kind.lower(), refs),
                "kind": "UNIQUE" if kind == "COMPOSITE_UNIQUE" else kind,
                "columns": local_columns, "requirement_ids": list(constraint["requirement_ids"]),
            }
            if kind == "FOREIGN_KEY":
                target_table, _, target_column = refs[1].partition(".")
                table_constraint.update({"columns": local_columns[:1], "referenced_table": target_table,
                                 "referenced_columns": [target_column], "on_delete": "RESTRICT", "on_update": "RESTRICT"})
            constraints.append(table_constraint)
        tables.append({
            "name": table_name, "description": entity["description"],
            "columns": columns, "constraints": sorted(constraints, key=lambda item: item["name"]),
            "requirement_ids": list(entity["requirement_ids"]),
        })

    for relationship in er_model["relationships"]:
        for side in ("parent", "child"):
            if relationship[f"{side}_min"] == 1 and (
                side == "parent" or relationship[f"{side}_max"] == "many"
            ):
                runtime_rules.append({
                    "scope": relationship["key"], "kind": f"{side}_minimum_participation", "value": 1,
                    "reason": "Foreign keys cannot require the existence of an associated row",
                    "requirement_ids": list(relationship["requirement_ids"]),
                })

    schema: SchemaIR = {
        **copy.deepcopy(relational),
        "ir_kind": "database_schema",
        "tables": tables, "runtime_rules": runtime_rules, "er_model": copy.deepcopy(er_model),
    }
    errors.extend(validate_schema_ir(schema))
    if errors:
        schema["status"] = "PROPOSED"
    return SchemaRefinementResult(schema, errors, warnings)


def _identifier(name: str, errors: list[str]) -> None:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        errors.append(f"SCHEMA_IR_IDENTIFIER_INVALID: {name!r}")


def _constraint_name(table: str, kind: str, values: Any) -> str:
    digest = hashlib.sha256(json.dumps([table, kind, values], sort_keys=True).encode()).hexdigest()[:12]
    return f"{table[:30]}_{kind[:16]}_{digest}"


def _check(table: str, column: str, op: str, value: Any, requirements: list[str]) -> dict[str, Any]:
    return {
        "name": _constraint_name(table, "check", [column, op, value]), "kind": "CHECK", "columns": [column],
        "predicate": {"column": column, "operator": op, "value": copy.deepcopy(value)},
        "requirement_ids": list(requirements),
    }


def validate_schema_ir(schema: SchemaIR) -> list[str]:
    """Validate relational consistency; database limits belong to lowering."""
    errors: list[str] = []
    if schema.get("ir_kind") != "database_schema":
        return ["SCHEMA_IR_KIND_INVALID: expected database-neutral Schema IR"]
    if "target" in schema or "policies" in schema:
        errors.append("SCHEMA_IR_DIALECT_SPECIFIC: target/storage policies do not belong in Schema IR")
    tables = {table["name"]: table for table in schema["tables"]}
    if len(tables) != len(schema["tables"]):
        errors.append("SCHEMA_IR_DUPLICATE_TABLE")
    types = {"string", "integer", "number", "boolean", "date", "datetime", "json", "uuid"}
    for table in schema["tables"]:
        _identifier(table["name"], errors)
        if any(key in table for key in ("engine", "character_set", "collation")):
            errors.append(f"SCHEMA_IR_DIALECT_SPECIFIC: {table['name']}")
        columns = {column["name"]: column for column in table["columns"]}
        if not columns or len(columns) != len(table["columns"]):
            errors.append(f"SCHEMA_IR_COLUMNS_INVALID: {table['name']}")
        if sum(item["kind"] == "PRIMARY_KEY" for item in table["constraints"]) != 1:
            errors.append(f"SCHEMA_IR_PRIMARY_KEY_INVALID: {table['name']}")
        for column in columns.values():
            _identifier(column["name"], errors)
            if "mysql_type" in column or "auto_increment" in column:
                errors.append(f"SCHEMA_IR_DIALECT_SPECIFIC: {table['name']}.{column['name']}")
            if column["type"] not in types or type(column["nullable"]) is not bool:
                errors.append(f"SCHEMA_IR_COLUMN_TYPE_INVALID: {table['name']}.{column['name']}")
            if column["generation"] not in {"none", "application_uuid"} or (column["generation"] == "application_uuid" and column["type"] != "uuid"):
                errors.append(f"SCHEMA_IR_GENERATION_INVALID: {table['name']}.{column['name']}")
            default = column["default"]
            if default is not None:
                kind = "json_literal" if column["type"] == "json" else "literal"
                value = default["value"]
                valid = (isinstance(value, str) if column["type"] in {"string", "uuid", "date", "datetime"} else
                         type(value) is int if column["type"] == "integer" else
                         type(value) in {int, float} if column["type"] == "number" else
                         type(value) is bool if column["type"] == "boolean" else True)
                if default["kind"] != kind or not valid:
                    errors.append(f"SCHEMA_IR_DEFAULT_INVALID: {table['name']}.{column['name']}")
        names: set[str] = set()
        for constraint in table["constraints"]:
            _identifier(constraint["name"], errors)
            if constraint["name"] in names:
                errors.append(f"SCHEMA_IR_CONSTRAINT_DUPLICATE: {constraint['name']}")
            names.add(constraint["name"])
            refs = constraint["columns"]
            if not refs or len(refs) != len(set(refs)) or set(refs) - columns.keys():
                errors.append(f"SCHEMA_IR_CONSTRAINT_COLUMN_INVALID: {constraint['name']}")
                continue
            kind = constraint["kind"]
            if kind == "PRIMARY_KEY" and any(columns[name]["nullable"] for name in refs):
                errors.append(f"SCHEMA_IR_NULLABLE_PRIMARY_KEY: {constraint['name']}")
            if kind == "CHECK":
                predicate = constraint["predicate"]
                if predicate["column"] not in refs or predicate["operator"] not in {"length_gte", "length_lte", "gte", "lte", "in"}:
                    errors.append(f"SCHEMA_IR_CHECK_INVALID: {constraint['name']}")
            elif kind == "FOREIGN_KEY":
                target = tables.get(constraint["referenced_table"])
                target_columns = {column["name"]: column for column in target["columns"]} if target else {}
                target_refs = constraint["referenced_columns"]
                if not target or len(refs) != len(target_refs) or len(target_refs) != len(set(target_refs)):
                    errors.append(f"SCHEMA_IR_FOREIGN_KEY_ARITY: {constraint['name']}")
                    continue
                if any(ref not in target_columns or columns[source]["type"] != target_columns[ref]["type"]
                       for source, ref in zip(refs, target_refs)):
                    errors.append(f"SCHEMA_IR_FOREIGN_KEY_TYPE: {constraint['name']}")
                if not any(item["kind"] in {"PRIMARY_KEY", "UNIQUE"} and item["columns"] == target_refs for item in target["constraints"]):
                    errors.append(f"SCHEMA_IR_FOREIGN_KEY_TARGET_NOT_UNIQUE: {constraint['name']}")
                if constraint["on_delete"] != "RESTRICT" or constraint["on_update"] != "RESTRICT":
                    errors.append(f"SCHEMA_IR_FOREIGN_KEY_ACTION_INVALID: {constraint['name']}")
            elif kind not in {"PRIMARY_KEY", "UNIQUE"}:
                errors.append(f"SCHEMA_IR_CONSTRAINT_KIND_INVALID: {constraint['name']}")
    return errors
