"""Separate ER models and relational table definitions; neither contains SQL text."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict



class ERAttribute(TypedDict):
    name: str
    type: str
    nullable: bool
    description: str
    properties: dict[str, Any]
    primary_key: bool
    origin: Literal["REQUIREMENT", "RELATIONSHIP", "SYSTEM"]
    references: str | None
    requirement_ids: list[str]
    read: list[str]
    write: list[str]


class EREntity(TypedDict):
    key: str
    description: str
    requirement_ids: list[str]
    fields: list[ERAttribute]
    read: list[str]
    write: list[str]


class ERRelationship(TypedDict):
    id: str
    key: str
    parent: str
    child: str
    type: Literal["ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_MANY"]
    parent_min: Literal[0, 1]
    parent_max: Literal["one", "many"]
    child_min: Literal[0, 1]
    child_max: Literal["one", "many"]
    description: str
    requirement_ids: list[str]
    evidence_requirement_ids: list[str]


class ERModelIR(TypedDict):
    ir_kind: Literal["er_model"]
    status: Literal["RESOLVED", "PROPOSED"]
    entities: list[EREntity]
    relationships: list[ERRelationship]
    contributions: dict[str, Any]
    conflicts: list[dict[str, Any]]
    access: dict[str, dict[str, list[str]]]


SchemaDataType = Literal["string", "integer", "number", "boolean", "date", "datetime", "json", "uuid"]


class SchemaDefault(TypedDict):
    kind: Literal["literal", "json_literal"]
    value: str | int | float | bool


class SchemaColumn(TypedDict):
    """Storage semantics are explicit; ER properties are never interpreted at lowering."""
    name: str
    type: SchemaDataType
    nullable: bool
    default: SchemaDefault | None
    generation: Literal["none", "application_uuid"]
    description: str
    origin: str
    requirement_ids: list[str]
    constraints: list[SchemaColumnConstraint]
    read: list[str]
    write: list[str]


class SchemaColumnConstraint(TypedDict):
    name: str
    kind: Literal["PRIMARY_KEY", "UNIQUE", "FOREIGN_KEY", "CHECK"]
    requirement_ids: list[str]
    comparison: NotRequired[Literal["exact", "case_insensitive"]]
    referenced_table: NotRequired[str]
    referenced_columns: NotRequired[list[str]]
    on_delete: NotRequired[Literal["RESTRICT"]]
    on_update: NotRequired[Literal["RESTRICT"]]
    predicate: NotRequired[dict[str, Any]]


class SchemaConstraint(TypedDict):
    name: str
    kind: Literal["PRIMARY_KEY", "UNIQUE", "FOREIGN_KEY", "CHECK"]
    columns: list[str]
    requirement_ids: list[str]
    comparison: NotRequired[Literal["exact", "case_insensitive"]]
    referenced_table: NotRequired[str]
    referenced_columns: NotRequired[list[str]]
    on_delete: NotRequired[Literal["RESTRICT"]]
    on_update: NotRequired[Literal["RESTRICT"]]
    # A structured predicate. A database strategy quotes its column and
    # literal operands; arbitrary SQL from model output is never accepted.
    predicate: NotRequired[dict[str, Any]]


class SchemaTable(TypedDict):
    name: str
    description: str
    columns: list[SchemaColumn]
    read: list[str]
    write: list[str]
    constraints: list[SchemaConstraint]
    requirement_ids: list[str]


class SchemaIR(TypedDict):
    ir_kind: Literal["database_schema"]
    status: Literal["RESOLVED", "PROPOSED"]
    tables: list[SchemaTable]
    traceability: dict[str, Any]


def table_constraints(table: SchemaTable) -> list[SchemaConstraint]:
    """Expand column-local constraints for consumers requiring column lists."""
    return [
        *table["constraints"],
        *({**constraint, "columns": [column["name"]]}
          for column in table["columns"] for constraint in column["constraints"]),
    ]
