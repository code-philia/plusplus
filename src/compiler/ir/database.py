"""Logical ER and refined MySQL schema contracts; neither contains SQL text."""

from typing import Any, Literal, NotRequired, TypedDict

ER_MODEL_VERSION = 1
SCHEMA_IR_VERSION = 1


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
    ir_version: int
    status: Literal["RESOLVED", "PROPOSED"]
    entities: list[EREntity]
    relationships: list[ERRelationship]


class MySQLType(TypedDict):
    name: Literal["CHAR", "VARCHAR", "LONGTEXT", "BIGINT", "DOUBLE", "BOOLEAN", "DATE", "DATETIME", "JSON"]
    length: int | None
    fractional_seconds: int | None
    unsigned: bool
    character_set: str | None
    collation: str | None


class SchemaDefault(TypedDict):
    kind: Literal["literal", "json_literal"]
    value: str | int | float | bool


class SchemaColumn(TypedDict):
    name: str
    logical_type: str
    mysql_type: MySQLType
    nullable: bool
    default: SchemaDefault | None
    auto_increment: bool
    description: str
    comment: str
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
    # A structured predicate. A future MySQL renderer quotes its column and
    # literal operands; arbitrary SQL from model output is never accepted.
    predicate: NotRequired[dict[str, Any]]


class SchemaTable(TypedDict):
    name: str
    description: str
    comment: str
    engine: Literal["InnoDB"]
    character_set: Literal["utf8mb4"]
    collation: Literal["utf8mb4_bin"]
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
    ir_version: int
    status: Literal["RESOLVED", "PROPOSED"]
    target: dict[str, str]
    policies: dict[str, Any]
    tables: list[SchemaTable]
    runtime_rules: list[RuntimeRule]
    traceability: dict[str, Any]
    # Derived compatibility view for current fixture/backend/SQLite consumers.
    # These fields are not a second physical schema authority.
    schema_version: int
    entities: list[EREntity]
    relationships: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    er_model: ERModelIR
