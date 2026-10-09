"""Compatibility imports for database Schema IR refinement."""

from .database_design.refinement import (
    SchemaRefinementResult,
    refine_er_model,
    refine_schema,
    validate_schema_ir,
)

__all__ = [
    "SchemaRefinementResult",
    "refine_er_model",
    "refine_schema",
    "validate_schema_ir",
]
