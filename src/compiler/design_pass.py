from __future__ import annotations

# Public compatibility module. The unified implementation lives in a focused module
# so contract discovery, call-tree planning, and flow binding share one interface.
from .design_flow_pass import (
    CALL_BINDING_INSTRUCTIONS,
    CALL_BINDING_SCHEMA,
    DesignPass,
    DesignPassResult,
    MODULE_CALL_TREE_INSTRUCTIONS,
    MODULE_CALL_TREE_SCHEMA,
    REQUIREMENT_CONTRACT_INSTRUCTIONS,
    REQUIREMENT_CONTRACT_SCHEMA,
    RETURN_BINDING_INSTRUCTIONS,
    RETURN_BINDING_SCHEMA,
    database_hash,
    design_hash,
    verify_design_manifest,
)

__all__ = [
    "CALL_BINDING_INSTRUCTIONS",
    "CALL_BINDING_SCHEMA",
    "DesignPass",
    "DesignPassResult",
    "MODULE_CALL_TREE_INSTRUCTIONS",
    "MODULE_CALL_TREE_SCHEMA",
    "REQUIREMENT_CONTRACT_INSTRUCTIONS",
    "REQUIREMENT_CONTRACT_SCHEMA",
    "RETURN_BINDING_INSTRUCTIONS",
    "RETURN_BINDING_SCHEMA",
    "database_hash",
    "design_hash",
    "verify_design_manifest",
]
