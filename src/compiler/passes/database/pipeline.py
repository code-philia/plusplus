"""Database design pipeline facade used by the compiler driver."""

from .semantic import DatabasePassResult, DatabaseSchemaPass, DatabaseSchemaState, database_traceability, schema_for_requirement, validate_database_schema


class DatabaseDesignPipeline(DatabaseSchemaPass):
    """Stable new-structure entry point preserving DatabaseSchemaPass behavior."""


__all__ = [
    "DatabaseDesignPipeline", "DatabasePassResult", "DatabaseSchemaPass", "DatabaseSchemaState",
    "database_traceability", "schema_for_requirement", "validate_database_schema",
]