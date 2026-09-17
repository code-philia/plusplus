"""Frozen contracts shared by Frontend Design and Frontend Lowering."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


FRONTEND_IR_SCHEMA_VERSION = 1
FRONTEND_MODULE_KINDS = {"PAGE", "LAYOUT", "COMPONENT", "STORE"}
UI_SCOPE_VALUES = {"UI_REQUIRED", "UI_AFFECTING", "NO_UI"}
VISUAL_BINDING_INTENTS = {
    "PAGE_REFERENCE",
    "COMPONENT_REFERENCE",
    "LAYOUT_REFERENCE",
    "STYLE_REFERENCE",
}


class FrontendDesignErrorCode(StrEnum):
    """Stable diagnostic codes for Frontend Design and visual-reference passes."""

    IR_INVALID = "ARC4101"
    SYMBOL_DUPLICATE = "ARC4102"
    REFERENCE_UNKNOWN = "ARC4103"
    ROUTE_CONFLICT = "ARC4104"
    COMPOSITION_CYCLE = "ARC4105"
    API_DEPENDENCY_INVALID = "ARC4106"
    REQUIREMENT_UNCOVERED = "ARC4107"
    BINDING_INCOMPATIBLE = "ARC4108"

    VISUAL_PATH_INVALID = "ARC4110"
    VISUAL_PATH_ESCAPE = "ARC4111"
    VISUAL_NOT_FOUND = "ARC4112"
    VISUAL_MEDIA_UNSUPPORTED = "ARC4113"
    VISUAL_TOO_LARGE = "ARC4114"
    VISUAL_READ_FAILED = "ARC4115"
    VISUAL_MODEL_UNAVAILABLE = "ARC4116"
    VISUAL_ANALYSIS_FAILED = "ARC4117"
    VISUAL_ANALYSIS_INVALID = "ARC4118"

    UI_SCOPE_MODEL_FAILED = "ARC4120"
    UI_SCOPE_DECISION_INVALID = "ARC4121"
    UI_SCOPE_REUSE_MISSING = "ARC4122"
    UI_SCOPE_CREATION_CONFLICT = "ARC4123"

    COMPONENT_MODEL_FAILED = "ARC4130"
    COMPONENT_DECISION_INVALID = "ARC4131"
    COMPONENT_REUSE_MISSING = "ARC4132"
    COMPONENT_CREATION_CONFLICT = "ARC4133"

    DATA_CONTRACT_MODEL_FAILED = "ARC4140"
    DATA_CONTRACT_DECISION_INVALID = "ARC4141"
    DATA_CONTRACT_CONFLICT = "ARC4142"
    API_BINDING_UNRESOLVED = "ARC4143"

    DUAL_DESIGN_INVALID = "ARC4150"


FRONTEND_ERROR_TITLES: dict[FrontendDesignErrorCode, str] = {
    FrontendDesignErrorCode.IR_INVALID: "FRONTEND_IR_INVALID",
    FrontendDesignErrorCode.SYMBOL_DUPLICATE: "FRONTEND_SYMBOL_DUPLICATE",
    FrontendDesignErrorCode.REFERENCE_UNKNOWN: "FRONTEND_REFERENCE_UNKNOWN",
    FrontendDesignErrorCode.ROUTE_CONFLICT: "FRONTEND_ROUTE_CONFLICT",
    FrontendDesignErrorCode.COMPOSITION_CYCLE: "FRONTEND_COMPOSITION_CYCLE",
    FrontendDesignErrorCode.API_DEPENDENCY_INVALID: "FRONTEND_API_DEPENDENCY_INVALID",
    FrontendDesignErrorCode.REQUIREMENT_UNCOVERED: "FRONTEND_REQUIREMENT_UNCOVERED",
    FrontendDesignErrorCode.BINDING_INCOMPATIBLE: "FRONTEND_BINDING_INCOMPATIBLE",
    FrontendDesignErrorCode.VISUAL_PATH_INVALID: "VISUAL_REFERENCE_PATH_INVALID",
    FrontendDesignErrorCode.VISUAL_PATH_ESCAPE: "VISUAL_REFERENCE_PATH_ESCAPE",
    FrontendDesignErrorCode.VISUAL_NOT_FOUND: "VISUAL_REFERENCE_NOT_FOUND",
    FrontendDesignErrorCode.VISUAL_MEDIA_UNSUPPORTED: "VISUAL_REFERENCE_MEDIA_UNSUPPORTED",
    FrontendDesignErrorCode.VISUAL_TOO_LARGE: "VISUAL_REFERENCE_TOO_LARGE",
    FrontendDesignErrorCode.VISUAL_READ_FAILED: "VISUAL_REFERENCE_READ_FAILED",
    FrontendDesignErrorCode.VISUAL_MODEL_UNAVAILABLE: "VISUAL_MODEL_UNAVAILABLE",
    FrontendDesignErrorCode.VISUAL_ANALYSIS_FAILED: "VISUAL_ANALYSIS_FAILED",
    FrontendDesignErrorCode.VISUAL_ANALYSIS_INVALID: "VISUAL_ANALYSIS_INVALID",
    FrontendDesignErrorCode.UI_SCOPE_MODEL_FAILED: "UI_SCOPE_MODEL_FAILED",
    FrontendDesignErrorCode.UI_SCOPE_DECISION_INVALID: "UI_SCOPE_DECISION_INVALID",
    FrontendDesignErrorCode.UI_SCOPE_REUSE_MISSING: "UI_SCOPE_REUSE_MISSING",
    FrontendDesignErrorCode.UI_SCOPE_CREATION_CONFLICT: "UI_SCOPE_CREATION_CONFLICT",
    FrontendDesignErrorCode.COMPONENT_MODEL_FAILED: "COMPONENT_MODEL_FAILED",
    FrontendDesignErrorCode.COMPONENT_DECISION_INVALID: "COMPONENT_DECISION_INVALID",
    FrontendDesignErrorCode.COMPONENT_REUSE_MISSING: "COMPONENT_REUSE_MISSING",
    FrontendDesignErrorCode.COMPONENT_CREATION_CONFLICT: "COMPONENT_CREATION_CONFLICT",
    FrontendDesignErrorCode.DATA_CONTRACT_MODEL_FAILED: "DATA_CONTRACT_MODEL_FAILED",
    FrontendDesignErrorCode.DATA_CONTRACT_DECISION_INVALID: "DATA_CONTRACT_DECISION_INVALID",
    FrontendDesignErrorCode.DATA_CONTRACT_CONFLICT: "DATA_CONTRACT_CONFLICT",
    FrontendDesignErrorCode.API_BINDING_UNRESOLVED: "API_BINDING_UNRESOLVED",
    FrontendDesignErrorCode.DUAL_DESIGN_INVALID: "DUAL_DESIGN_INVALID",
}


@dataclass(slots=True)
class FrontendDesignIssue:
    code: FrontendDesignErrorCode
    message: str
    phase: str
    blame_symbol: str
    severity: str = "ERROR"
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return FRONTEND_ERROR_TITLES[self.code]

    def format(self) -> str:
        return f"{self.code.value} {self.title}: {self.message}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "title": self.title,
            "severity": self.severity,
            "phase": self.phase,
            "blame_symbol": self.blame_symbol,
            "message": self.message,
            "context": copy.deepcopy(self.context),
        }


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _string_list(*, max_items: int = 64, max_length: int = 240) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": max_items,
        "items": {"type": "string", "minLength": 1, "maxLength": max_length},
    }


SEMANTIC_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["semantic_id", "name", "type", "required"],
    "properties": {
        "semantic_id": {
            "type": "string",
            "pattern": r"^[a-z][a-z0-9_.]*$",
            "maxLength": 120,
        },
        "name": {"type": "string", "pattern": r"^[a-z][a-z0-9_]*$", "maxLength": 64},
        "type": {"type": "string", "minLength": 1, "maxLength": 160},
        "required": {"type": "boolean"},
    },
}

VISUAL_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "reference_id",
        "regions",
        "visible_controls",
        "layout_cues",
        "style_cues",
        "text_cues",
    ],
    "properties": {
        "reference_id": {"type": "string", "pattern": r"^VISUAL\.[0-9a-f]{16}$"},
        "regions": _string_list(),
        "visible_controls": _string_list(),
        "layout_cues": _string_list(),
        "style_cues": _string_list(),
        "text_cues": _string_list(),
    },
}

VISUAL_REFERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id",
        "source_path",
        "aliases",
        "sha256",
        "media_type",
        "byte_size",
        "requirement_ids",
        "analysis",
    ],
    "properties": {
        "id": {"type": "string", "pattern": r"^VISUAL\.[0-9a-f]{16}$"},
        "source_path": {"type": "string", "minLength": 1},
        "aliases": _string_list(),
        "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
        "media_type": {
            "type": "string",
            "enum": ["image/gif", "image/jpeg", "image/png", "image/webp"],
        },
        "byte_size": {"type": "integer", "minimum": 1},
        "requirement_ids": _string_list(),
        "analysis": VISUAL_ANALYSIS_SCHEMA,
    },
}

RENDER_OBLIGATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "kind", "label", "semantic_id", "required"],
    "properties": {
        "id": {"type": "string", "pattern": r"^[a-z][a-z0-9_]*$", "maxLength": 80},
        "kind": {
            "type": "string",
            "enum": ["ACTION", "FEEDBACK", "FIELD", "NAVIGATION", "REGION", "TEXT"],
        },
        "label": {"type": "string", "minLength": 1, "maxLength": 200},
        "semantic_id": _nullable(
            {"type": "string", "pattern": r"^[a-z][a-z0-9_.]*$", "maxLength": 120}
        ),
        "required": {"type": "boolean"},
    },
}

EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "payload_type", "async"],
    "properties": {
        "name": {"type": "string", "pattern": r"^[a-z][A-Za-z0-9]*$", "maxLength": 64},
        "payload_type": _nullable({"type": "string", "minLength": 1, "maxLength": 120}),
        "async": {"type": "boolean"},
    },
}

NAVIGATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["trigger", "target", "condition"],
    "properties": {
        "trigger": {"type": "string", "minLength": 1, "maxLength": 120},
        "target": {"type": "string", "minLength": 1, "maxLength": 200},
        "condition": _nullable({"type": "string", "minLength": 1, "maxLength": 300}),
    },
}

LAYOUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id",
        "spec",
        "requirement_ids",
        "page_ids",
        "component_ids",
        "render_obligations",
        "visual_reference_ids",
    ],
    "properties": {
        "id": {"type": "string", "pattern": r"^LAYOUT\.[A-Za-z][A-Za-z0-9]*$"},
        "spec": {"type": "string", "minLength": 1, "maxLength": 800},
        "requirement_ids": _string_list(),
        "page_ids": _string_list(),
        "component_ids": _string_list(),
        "render_obligations": {"type": "array", "items": RENDER_OBLIGATION_SCHEMA},
        "visual_reference_ids": _string_list(),
    },
}

PAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id",
        "spec",
        "route",
        "route_inputs",
        "requirement_ids",
        "layout_id",
        "component_ids",
        "api_dependencies",
        "store_dependencies",
        "render_obligations",
        "navigation",
        "visual_reference_ids",
    ],
    "properties": {
        "id": {"type": "string", "pattern": r"^PAGE\.[A-Za-z][A-Za-z0-9]*$"},
        "spec": {"type": "string", "minLength": 1, "maxLength": 800},
        "route": {"type": "string", "pattern": r"^/", "maxLength": 240},
        "route_inputs": {"type": "array", "items": SEMANTIC_FIELD_SCHEMA},
        "requirement_ids": _string_list(),
        "layout_id": _nullable(
            {"type": "string", "pattern": r"^LAYOUT\.[A-Za-z][A-Za-z0-9]*$"}
        ),
        "component_ids": _string_list(),
        "api_dependencies": _string_list(),
        "store_dependencies": _string_list(),
        "render_obligations": {"type": "array", "items": RENDER_OBLIGATION_SCHEMA},
        "navigation": {"type": "array", "items": NAVIGATION_SCHEMA},
        "visual_reference_ids": _string_list(),
    },
}

COMPONENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id",
        "spec",
        "scope",
        "owner_page_id",
        "owner_layout_id",
        "inputs",
        "events",
        "render_obligations",
        "visual_reference_ids",
    ],
    "properties": {
        "id": {"type": "string", "pattern": r"^COMPONENT\.[A-Za-z][A-Za-z0-9]*$"},
        "spec": {"type": "string", "minLength": 1, "maxLength": 800},
        "scope": {"type": "string", "enum": ["LAYOUT", "PAGE", "SHARED"]},
        "owner_page_id": _nullable({"type": "string", "pattern": r"^PAGE\."}),
        "owner_layout_id": _nullable({"type": "string", "pattern": r"^LAYOUT\."}),
        "inputs": {"type": "array", "items": SEMANTIC_FIELD_SCHEMA},
        "events": {"type": "array", "items": EVENT_SCHEMA},
        "render_obligations": {"type": "array", "items": RENDER_OBLIGATION_SCHEMA},
        "visual_reference_ids": _string_list(),
    },
}

STORE_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "input_type"],
    "properties": {
        "name": {"type": "string", "pattern": r"^[a-z][A-Za-z0-9]*$", "maxLength": 64},
        "input_type": _nullable({"type": "string", "minLength": 1, "maxLength": 120}),
    },
}

STORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "spec", "state", "actions", "requirement_ids", "consumer_ids"],
    "properties": {
        "id": {"type": "string", "pattern": r"^STORE\.[A-Za-z][A-Za-z0-9]*$"},
        "spec": {"type": "string", "minLength": 1, "maxLength": 800},
        "state": {"type": "array", "items": SEMANTIC_FIELD_SCHEMA},
        "actions": {"type": "array", "items": STORE_ACTION_SCHEMA},
        "requirement_ids": _string_list(),
        "consumer_ids": _string_list(),
    },
}

LOCAL_DATA_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "owner_component_id", "fields"],
    "properties": {
        "id": {"type": "string", "pattern": r"^UI_TYPE\.[A-Za-z][A-Za-z0-9]*$"},
        "owner_component_id": {"type": "string", "pattern": r"^COMPONENT\."},
        "fields": {"type": "array", "items": SEMANTIC_FIELD_SCHEMA},
    },
}

COMPOSITION_EDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["parent_id", "child_id", "order"],
    "properties": {
        "parent_id": {"type": "string", "minLength": 1},
        "child_id": {"type": "string", "minLength": 1},
        "order": {"type": "integer", "minimum": 0},
    },
}

API_BINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_semantic_id", "target_semantic_id"],
    "properties": {
        "source_semantic_id": {"type": "string", "minLength": 1},
        "target_semantic_id": {"type": "string", "minLength": 1},
    },
}

API_DEPENDENCY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["consumer_id", "api_id", "bindings"],
    "properties": {
        "consumer_id": {"type": "string", "minLength": 1},
        "api_id": {"type": "string", "minLength": 1},
        "bindings": {"type": "array", "items": API_BINDING_SCHEMA},
    },
}

REQUIREMENT_LINK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirement_id", "ui_scope", "symbol_ids", "visual_reference_ids"],
    "properties": {
        "requirement_id": {"type": "string", "minLength": 1},
        "ui_scope": {"type": "string", "enum": sorted(UI_SCOPE_VALUES)},
        "symbol_ids": _string_list(),
        "visual_reference_ids": _string_list(),
    },
}

FRONTEND_DESIGN_IR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "visual_references",
        "layouts",
        "pages",
        "components",
        "stores",
        "local_data_contracts",
        "composition_edges",
        "api_dependencies",
        "requirement_links",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": FRONTEND_IR_SCHEMA_VERSION},
        "visual_references": {"type": "array", "items": VISUAL_REFERENCE_SCHEMA},
        "layouts": {"type": "array", "items": LAYOUT_SCHEMA},
        "pages": {"type": "array", "items": PAGE_SCHEMA},
        "components": {"type": "array", "items": COMPONENT_SCHEMA},
        "stores": {"type": "array", "items": STORE_SCHEMA},
        "local_data_contracts": {"type": "array", "items": LOCAL_DATA_CONTRACT_SCHEMA},
        "composition_edges": {"type": "array", "items": COMPOSITION_EDGE_SCHEMA},
        "api_dependencies": {"type": "array", "items": API_DEPENDENCY_SCHEMA},
        "requirement_links": {"type": "array", "items": REQUIREMENT_LINK_SCHEMA},
    },
}

FRONTEND_DESIGN_TABLE_SCHEMAS: dict[str, dict[str, Any]] = {
    "visual_references": {"type": "array", "items": VISUAL_REFERENCE_SCHEMA},
    "layouts": {"type": "array", "items": LAYOUT_SCHEMA},
    "pages": {"type": "array", "items": PAGE_SCHEMA},
    "components": {"type": "array", "items": COMPONENT_SCHEMA},
    "stores": {"type": "array", "items": STORE_SCHEMA},
    "local_data_contracts": {"type": "array", "items": LOCAL_DATA_CONTRACT_SCHEMA},
    "composition_edges": {"type": "array", "items": COMPOSITION_EDGE_SCHEMA},
    "api_dependencies": {"type": "array", "items": API_DEPENDENCY_SCHEMA},
}


def repair_schema_shape(value: Any, schema: dict[str, Any]) -> Any:
    """Apply lossless/tolerant size and property repairs before strict validation.

    This intentionally does not invent required semantic values or repair references.
    Individual Frontend Design passes own those decisions. It only removes unknown
    object properties, clamps arrays, and truncates strings to frozen IR limits.
    """

    if "anyOf" in schema:
        branches = [item for item in schema.get("anyOf", []) if isinstance(item, dict)]
        if value is None and any(item.get("type") == "null" for item in branches):
            return None
        branch = next((item for item in branches if item.get("type") != "null"), None)
        return repair_schema_shape(value, branch) if branch is not None else copy.deepcopy(value)

    expected_type = schema.get("type")
    if expected_type == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return copy.deepcopy(value)
        return {
            key: repair_schema_shape(item, properties[key])
            for key, item in value.items()
            if key in properties and isinstance(properties[key], dict)
        }
    if expected_type == "array" and isinstance(value, list):
        maximum = schema.get("maxItems")
        items = value[:maximum] if isinstance(maximum, int) else value
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            return copy.deepcopy(items)
        return [repair_schema_shape(item, item_schema) for item in items]
    if expected_type == "string" and isinstance(value, str):
        maximum = schema.get("maxLength")
        return value[:maximum] if isinstance(maximum, int) else value
    return copy.deepcopy(value)


def schema_shape_errors(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the frozen JSON-Schema subset without adding a runtime dependency."""

    if "anyOf" in schema:
        branches = schema.get("anyOf", [])
        if any(not schema_shape_errors(value, branch, path) for branch in branches):
            return []
        return [f"{path} does not match any allowed schema"]

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        branches = [{**schema, "type": item} for item in expected_type]
        if any(not schema_shape_errors(value, branch, path) for branch in branches):
            return []
        return [f"{path} has an invalid type"]

    type_ok = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(str(expected_type), True)
    if not type_ok:
        return [f"{path} must be {expected_type}"]

    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']!r}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        missing = sorted(required - set(value))
        if missing:
            errors.append(f"{path} is missing required fields: {missing}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                errors.append(f"{path} has unexpected fields: {extra}")
        for key, item in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                errors.extend(schema_shape_errors(item, child_schema, f"{path}.{key}"))
    elif isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path} must contain at least {minimum} items")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path} must contain at most {maximum} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(schema_shape_errors(item, item_schema, f"{path}[{index}]"))
    elif isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path} must contain at least {minimum} characters")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path} must contain at most {maximum} characters")
        if isinstance(pattern, str):
            import re

            if re.search(pattern, value) is None:
                errors.append(f"{path} does not match {pattern!r}")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path} must be at least {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path} must be at most {maximum}")
    return errors
