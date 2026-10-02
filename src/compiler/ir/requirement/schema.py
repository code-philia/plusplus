from __future__ import annotations

REQUIREMENT_IR_SCHEMA_VERSION = 1
REQUIREMENT_NODE_TYPES = frozenset({"FOLDER", "ATOMIC"})

REQUIREMENT_IR_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "root_id", "node_order", "nodes"],
    "properties": {
        "schema_version": {"type": "integer", "const": REQUIREMENT_IR_SCHEMA_VERSION},
        "root_id": {"type": ["string", "null"]},
        "node_order": {"type": "array", "items": {"type": "string"}},
        "atomic_units": {"type": "array", "items": {"type": "string"}},
        "folder_nodes": {"type": "array", "items": {"type": "string"}},
        "nodes": {"type": "object"},
    },
}


def validate_requirement_ir_shape(value: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["Requirement IR must be an object"]
    if not isinstance(value.get("root_id"), (str, type(None))):
        errors.append("root_id must be a string or null")
    if not isinstance(value.get("nodes", {}), dict):
        errors.append("nodes must be an object")
    return errors


__all__ = ["REQUIREMENT_IR_SCHEMA", "REQUIREMENT_IR_SCHEMA_VERSION", "REQUIREMENT_NODE_TYPES", "validate_requirement_ir_shape"]