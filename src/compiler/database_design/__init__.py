"""Database design: requirement-driven ER discovery and Schema IR refinement."""

from .contracts import (
    ENTITY_DECISION_SCHEMA,
    ENTITY_INSTRUCTIONS,
    FIELD_DECISION_SCHEMA,
    FIELD_DECLARATION_SCHEMA,
    FIELD_INSTRUCTIONS,
    FIELD_PROPERTIES_SCHEMA,
    RELATIONSHIP_DECISION_SCHEMA,
    RELATIONSHIP_INSTRUCTIONS,
)
from .queries import database_traceability, schema_for_requirement
from .refinement import SchemaRefinementResult, refine_er_model, refine_schema, validate_schema_ir
from .stage import DatabasePassResult, DatabaseSchemaPass
from .state import DatabaseSchemaState
from .validation import validate_database_schema

__all__ = [
    "ENTITY_DECISION_SCHEMA",
    "ENTITY_INSTRUCTIONS",
    "FIELD_DECLARATION_SCHEMA",
    "FIELD_PROPERTIES_SCHEMA",
    "FIELD_DECISION_SCHEMA",
    "FIELD_INSTRUCTIONS",
    "RELATIONSHIP_DECISION_SCHEMA",
    "RELATIONSHIP_INSTRUCTIONS",
    "database_traceability",
    "schema_for_requirement",
    "SchemaRefinementResult",
    "refine_er_model",
    "refine_schema",
    "validate_schema_ir",
    "DatabasePassResult",
    "DatabaseSchemaPass",
    "DatabaseSchemaState",
    "validate_database_schema",
]
