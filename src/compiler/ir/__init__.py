"""Shared, JSON-serializable intermediate representation contracts.

Stage algorithms live outside this package. Add new IR families here so their
producers and consumers can share the same definitions.
"""

from .database import (
    ERAttribute, EREntity, ERModelIR, ERRelationship,
    SchemaColumn, SchemaConstraint, SchemaDefault, SchemaIR, SchemaTable,
    MySQLType, RuntimeRule,
)

__all__ = [
    "ERAttribute", "EREntity", "ERModelIR", "ERRelationship", "MySQLType",
    "RuntimeRule", "SchemaColumn", "SchemaConstraint", "SchemaDefault",
    "SchemaIR", "SchemaTable",
]
