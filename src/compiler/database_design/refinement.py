"""Deterministically refine a completed ER model into a MySQL-ready Schema IR.

The existing relationship mapper supplies the relational projection. This
pass fixes column storage types and structured constraints; it emits no SQL.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..ir import ERModelIR, MySQLType, SchemaIR
from ..ir.database import SCHEMA_IR_VERSION
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
    the new MySQL schema agree on table/column identities.
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
    """Use declared ER constraints and explicit fixed storage policies only."""
    errors: list[str] = []
    warnings: list[str] = []
    tables: list[dict[str, Any]] = []
    runtime_rules: list[dict[str, Any]] = []
    fields = {
        f"{entity['key']}.{column['name']}": column
        for entity in relational["entities"] for column in entity["fields"]
    }
    mapped_types: dict[str, MySQLType] = {}
    for reference, column in fields.items():
        if column["type"] != "foreign_key":
            mapped_types[reference] = _mysql_type(column)
    for reference, column in fields.items():
        if column["type"] == "foreign_key":
            target = column.get("references")
            if target not in mapped_types:
                errors.append(f"SCHEMA_IR_REFERENCE_INVALID: {reference} -> {target}")
            else:
                mapped_types[reference] = copy.deepcopy(mapped_types[target])

    for entity in sorted(relational["entities"], key=lambda item: item["key"]):
        table_name = entity["key"]
        _identifier(table_name, errors)
        columns: list[dict[str, Any]] = []
        constraints: list[dict[str, Any]] = []
        for column in sorted(entity["fields"], key=lambda item: item["name"]):
            name = column["name"]
            _identifier(name, errors)
            reference = f"{table_name}.{name}"
            mysql_type = mapped_types.get(reference)
            if mysql_type is None:
                continue
            if column["type"] == "number":
                warnings.append(f"SCHEMA_IR_APPROXIMATE_NUMBER: {reference} uses DOUBLE; exact decimal semantics are not present in the ER type")
            props = column.get("properties", {})
            default = props.get("default")
            if mysql_type["name"] == "BIGINT":
                for value in (props.get("minimum"), props.get("maximum"), default):
                    if value is not None and not -(2**63) <= value <= 2**63 - 1:
                        errors.append(f"SCHEMA_IR_INTEGER_RANGE: {reference} exceeds signed BIGINT")
            columns.append({
                "name": name, "logical_type": column["type"], "mysql_type": mysql_type,
                "nullable": column["nullable"],
                "default": None if default is None else {
                    "kind": "json_literal" if column["type"] == "json" else "literal", "value": default,
                },
                "auto_increment": False, "description": column["description"],
                "comment": column["description"][:1024], "origin": column["origin"],
                "properties": copy.deepcopy(props), "requirement_ids": list(column["requirement_ids"]),
            })
            if len(column["description"]) > 1024:
                warnings.append(f"SCHEMA_IR_COMMENT_TRUNCATED: {reference}; full description retained")
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
                        "reason": "Python regex/semantic format/relative time is not a portable MySQL CHECK",
                        "requirement_ids": list(column["requirement_ids"]),
                    })
            if column["type"] == "boolean":
                constraints.append(_check(table_name, name, "in", [0, 1], column["requirement_ids"]))

        for constraint in relational["constraints"]:
            refs = constraint["fields"]
            if not refs or refs[0].partition(".")[0] != table_name or constraint["type"] == "NOT_NULL":
                continue
            kind = constraint["type"]
            local_columns = [ref.partition(".")[2] for ref in refs]
            physical = {
                "name": _constraint_name(table_name, kind.lower(), refs),
                "kind": "UNIQUE" if kind == "COMPOSITE_UNIQUE" else kind,
                "columns": local_columns, "requirement_ids": list(constraint["requirement_ids"]),
            }
            if kind == "FOREIGN_KEY":
                target_table, _, target_column = refs[1].partition(".")
                physical.update({"columns": local_columns[:1], "referenced_table": target_table,
                                 "referenced_columns": [target_column], "on_delete": "RESTRICT", "on_update": "RESTRICT"})
            constraints.append(physical)
        tables.append({
            "name": table_name, "description": entity["description"], "comment": entity["description"][:2048],
            "engine": "InnoDB", "character_set": "utf8mb4", "collation": "utf8mb4_bin",
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
        "ir_kind": "database_schema", "ir_version": SCHEMA_IR_VERSION,
        "target": {"dialect": "mysql", "version": "8.4"},
        "policies": {
            "version": 1, "integer_storage": "signed BIGINT", "number_storage": "approximate DOUBLE",
            "unbounded_text_storage": "LONGTEXT", "uuid_storage": "CHAR(36) ascii_bin; application-generated",
            "datetime_storage": "DATETIME(6); application normalizes to UTC",
            "foreign_key_actions": "RESTRICT", "schema_sql": "tables first, foreign keys second",
            "exact_decimal": "not inferred from number; requires a future explicit ER decimal type",
        },
        "tables": tables, "runtime_rules": runtime_rules, "er_model": copy.deepcopy(er_model),
    }
    errors.extend(validate_schema_ir(schema))
    if errors:
        schema["status"] = "PROPOSED"
    return SchemaRefinementResult(schema, errors, warnings)


def _mysql_type(column: dict[str, Any]) -> MySQLType:
    kind = column["type"]
    props = column.get("properties", {})
    storage: MySQLType = {
        "name": "LONGTEXT", "length": None, "fractional_seconds": None,
        "unsigned": False, "character_set": None, "collation": None,
    }
    if kind == "string":
        length = props.get("max_length")
        if length is None and props.get("enum"):
            length = max(1, max(map(len, props["enum"])))
        # utf8mb4: wider values use LONGTEXT plus a length CHECK. Even smaller
        # VARCHAR columns are subject to the table's total row-size budget.
        if isinstance(length, int) and 1 <= length <= 16383:
            storage.update(name="VARCHAR", length=length)
    elif kind == "uuid":
        storage.update(name="CHAR", length=36, character_set="ascii", collation="ascii_bin")
    else:
        storage["name"] = {"integer": "BIGINT", "number": "DOUBLE", "boolean": "BOOLEAN",
                           "date": "DATE", "datetime": "DATETIME", "json": "JSON"}[kind]
        if kind == "datetime":
            storage["fractional_seconds"] = 6
    return storage


def _identifier(name: str, errors: list[str]) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
        errors.append(f"SCHEMA_IR_IDENTIFIER_INVALID: {name!r}; MySQL identifiers must fit 64 characters")


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
    """Check the concrete constraints needed by a later MySQL file renderer."""
    errors: list[str] = []
    names: set[str] = set()
    tables = {table["name"]: table for table in schema["tables"]}
    if len(tables) != len(schema["tables"]):
        errors.append("SCHEMA_IR_DUPLICATE_TABLE")
    for table in schema["tables"]:
        columns = {column["name"]: column for column in table["columns"]}
        if len(columns) != len(table["columns"]):
            errors.append(f"SCHEMA_IR_DUPLICATE_COLUMN: {table['name']}")
        if sum(constraint["kind"] == "PRIMARY_KEY" for constraint in table["constraints"]) != 1:
            errors.append(f"SCHEMA_IR_PRIMARY_KEY_INVALID: {table['name']}")
        # Conservative upper bound for VARCHAR + fixed-width inline data.
        inline_bytes = 0
        for column in columns.values():
            mysql = column["mysql_type"]
            inline_bytes += (mysql["length"] * 4 + 2 if mysql["name"] == "VARCHAR" else
                             mysql["length"] if mysql["name"] == "CHAR" else 16)
        if inline_bytes > 65000:
            errors.append(f"SCHEMA_IR_ROW_TOO_WIDE: {table['name']}; reduce VARCHAR bounds")
        for constraint in table["constraints"]:
            if constraint["name"] in names:
                errors.append(f"SCHEMA_IR_CONSTRAINT_DUPLICATE: {constraint['name']}")
            names.add(constraint["name"])
            if set(constraint["columns"]) - columns.keys():
                errors.append(f"SCHEMA_IR_CONSTRAINT_COLUMN_INVALID: {constraint['name']}")
                continue
            if constraint["kind"] in {"PRIMARY_KEY", "UNIQUE"}:
                key_bytes = 0
                for name in constraint["columns"]:
                    mysql = columns[name]["mysql_type"]
                    if mysql["name"] in {"LONGTEXT", "JSON"}:
                        errors.append(f"SCHEMA_IR_KEY_TYPE_INVALID: {table['name']}.{name}")
                    key_bytes += (mysql["length"] * (1 if mysql["character_set"] == "ascii" else 4)
                                  if mysql["length"] else 8)
                if key_bytes > 3072:
                    errors.append(f"SCHEMA_IR_KEY_TOO_WIDE: {constraint['name']}")
            if constraint["kind"] == "FOREIGN_KEY":
                target = tables.get(constraint["referenced_table"])
                target_columns = {column["name"]: column for column in target["columns"]} if target else {}
                if len(constraint["columns"]) != len(constraint["referenced_columns"]):
                    errors.append(f"SCHEMA_IR_FOREIGN_KEY_ARITY: {constraint['name']}")
                    continue
                for source, reference in zip(constraint["columns"], constraint["referenced_columns"]):
                    if reference not in target_columns or columns[source]["mysql_type"] != target_columns[reference]["mysql_type"]:
                        errors.append(f"SCHEMA_IR_FOREIGN_KEY_TYPE: {constraint['name']}")
                target_key = constraint["referenced_columns"]
                if target and not any(item["kind"] in {"PRIMARY_KEY", "UNIQUE"} and item["columns"] == target_key
                                      for item in target["constraints"]):
                    errors.append(f"SCHEMA_IR_FOREIGN_KEY_TARGET_NOT_UNIQUE: {constraint['name']}")
    return errors
