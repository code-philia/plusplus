"""Logical ER and database-neutral relational schema contracts; neither contains SQL text."""

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


class EREntity(TypedDict):
    key: str
    description: str
    requirement_ids: list[str]
    fields: list[ERAttribute]


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


SchemaDataType = Literal["string", "integer", "number", "boolean", "date", "datetime", "json", "uuid"]


class SchemaDefault(TypedDict):
    kind: Literal["literal", "json_literal"]
    value: str | int | float | bool


class SchemaColumn(TypedDict):
    name: str
    type: SchemaDataType
    nullable: bool
    default: SchemaDefault | None
    generation: Literal["none", "application_uuid"]
    description: str
    origin: str
    properties: dict[str, Any]
    requirement_ids: list[str]


class SchemaConstraint(TypedDict):
    name: str
    kind: Literal["PRIMARY_KEY", "UNIQUE", "FOREIGN_KEY", "CHECK"]
    columns: list[str]
    requirement_ids: list[str]
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
    constraints: list[SchemaConstraint]
    requirement_ids: list[str]


class RuntimeRule(TypedDict):
    scope: str
    kind: str
    value: Any
    reason: str
    requirement_ids: list[str]


class SchemaIR(TypedDict):
    ir_kind: Literal["database_schema"]
    status: Literal["RESOLVED", "PROPOSED"]
    tables: list[SchemaTable]
    runtime_rules: list[RuntimeRule]
    traceability: dict[str, Any]
    # Derived compatibility view for existing requirement/backend consumers.
    # These fields are not a second schema authority.
    entities: list[EREntity]
    relationships: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    er_model: ERModelIR
