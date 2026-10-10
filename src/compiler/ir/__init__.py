"""Shared, JSON-serializable intermediate representation contracts.

Stage algorithms live outside this package. Add new IR families here so their
producers and consumers can share the same definitions.
"""

from .integration import PendingCapability
from .database import (
    ERAttribute, EREntity, ERModelIR, ERRelationship,
    SchemaColumn, SchemaConstraint, SchemaDefault, SchemaIR, SchemaTable,
    SchemaDataType, SchemaColumnConstraint, table_constraints,
)
from .backend import (
    BackendIR, BackendModule, APIModule, FunctionModule,
    DatabaseModule, ModuleBase, BackendField, BackendType, BackendTypeDefinition,
    AcceptanceCriterion, ModuleCall, DataAccess, APIRequestField, module_fields,
)
from .frontend import (
    FrontendIR, UINode, UIData, UIEvent, UIFunction, UICall, UIValue,
    LiteralValue, ReferenceValue, UIRepeat, UIVisualReference, RequirementUIAssociation,
)

__all__ = [
    "PendingCapability",
    "ERAttribute", "EREntity", "ERModelIR", "ERRelationship", "SchemaDataType",
    "SchemaColumnConstraint", "table_constraints", "SchemaColumn", "SchemaConstraint", "SchemaDefault",
    "SchemaIR", "SchemaTable",
    "BackendIR", "BackendModule", "APIModule",
    "FunctionModule", "DatabaseModule", "ModuleBase", "BackendField",
    "BackendType", "BackendTypeDefinition", "AcceptanceCriterion", "ModuleCall",
    "DataAccess", "APIRequestField", "module_fields",
    "FrontendIR", "UINode", "UIData", "UIEvent", "UIFunction", "UICall",
    "UIValue", "LiteralValue", "ReferenceValue", "UIRepeat", "UIVisualReference",
    "RequirementUIAssociation",
]
