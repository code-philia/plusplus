"""Compatibility imports for :mod:`compiler.database_design`."""

from .database_design import (
    DatabasePassResult,
    DatabaseSchemaPass,
    DatabaseSchemaState,
    ENTITY_DECISION_SCHEMA,
    ENTITY_INSTRUCTIONS,
    FIELD_DECISION_SCHEMA,
    FIELD_INSTRUCTIONS,
    RELATIONSHIP_DECISION_SCHEMA,
    RELATIONSHIP_INSTRUCTIONS,
    database_traceability,
    schema_for_requirement,
    validate_database_schema,
)

__all__ = [
    "DatabasePassResult",
    "DatabaseSchemaPass",
    "DatabaseSchemaState",
    "ENTITY_DECISION_SCHEMA",
    "ENTITY_INSTRUCTIONS",
    "FIELD_DECISION_SCHEMA",
    "FIELD_INSTRUCTIONS",
    "RELATIONSHIP_DECISION_SCHEMA",
    "RELATIONSHIP_INSTRUCTIONS",
    "database_traceability",
    "schema_for_requirement",
    "validate_database_schema",
]
