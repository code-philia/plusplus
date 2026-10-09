"""Shared, JSON-serializable intermediate representation contracts.

Stage algorithms live outside this package. Add new IR families here so their
producers and consumers can share the same definitions.
"""

from .database import (
    ERAttribute, EREntity, ERModelIR, ERRelationship,
    SchemaColumn, SchemaConstraint, SchemaDefault, SchemaIR, SchemaTable,
    SchemaDataType, RuntimeRule,
)
from .backend import (
    BackendIR, BackendModule, APIModule, FunctionModule,
    DatabaseModule, ModuleBase, BackendField, BackendType, BackendTypeDefinition,
    AcceptanceCriterion, ModuleCall, DataAccess, RequirementBlueprint, APIEntryPoint,
)

__all__ = [
    "ERAttribute", "EREntity", "ERModelIR", "ERRelationship", "SchemaDataType",
    "RuntimeRule", "SchemaColumn", "SchemaConstraint", "SchemaDefault",
    "SchemaIR", "SchemaTable",
    "BackendIR", "BackendModule", "APIModule",
    "FunctionModule", "DatabaseModule", "ModuleBase", "BackendField",
    "BackendType", "BackendTypeDefinition", "AcceptanceCriterion", "ModuleCall",
    "DataAccess", "RequirementBlueprint", "APIEntryPoint",
]
