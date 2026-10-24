"""Logical field properties and literal rules shared by decision and schema checks."""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

from .contracts import FIELD_TYPES


def _field_property_errors(entity_key: str, field_item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    name = str(field_item.get("name", ""))
    field_type = field_item.get("type")
    if not isinstance(field_type, str) or field_type not in FIELD_TYPES:
        return [f"invalid field type: {entity_key}.{name}"]
    properties = field_item.get("properties")
    if not isinstance(properties, dict):
        return [f"field properties must be an object: {entity_key}.{name}"]
    unknown = set(properties) - {
        "min_length", "max_length", "enum", "minimum", "maximum", "default", "unique", "comparison",
    }
    if unknown:
        errors.append(f"unknown field properties: {entity_key}.{name} -> {', '.join(sorted(unknown))}")
    for key in ("min_length", "max_length"):
        value = properties.get(key)
        if value is not None and (type(value) is not int or value < 0):
            errors.append(f"{key} must be a nonnegative integer: {entity_key}.{name}")
    for key in ("minimum", "maximum"):
        value = properties.get(key)
        if value is not None and (
            type(value) not in {int, float} or not math.isfinite(value)
            or (field_type == "integer" and int(value) != value)
        ):
            errors.append(f"invalid numeric {key}: {entity_key}.{name}")
    if properties.get("unique") is not None and type(properties["unique"]) is not bool:
        errors.append(f"unique must be a boolean: {entity_key}.{name}")
    if properties.get("comparison") not in (None, "exact", "case_insensitive"):
        errors.append(f"invalid comparison: {entity_key}.{name}")
    if properties.get("comparison") is not None and field_type != "string":
        errors.append(f"comparison requires a string field: {entity_key}.{name}")
    if properties.get("comparison") is not None and properties.get("unique") is not True:
        errors.append(f"comparison requires unique=true: {entity_key}.{name}")
    enum = properties.get("enum")
    if enum is not None and (
        not isinstance(enum, list) or not enum
        or any(not isinstance(value, str) for value in enum)
        or any(isinstance(value, str) and "\x00" in value for value in enum)
        or len(enum) != len(set(value for value in enum if isinstance(value, str)))
    ):
        errors.append(f"enum must be a nonempty list of distinct strings: {entity_key}.{name}")
    default = properties.get("default")
    if default is not None:
        valid_default = (
            (field_type in {"string", "date", "datetime", "uuid", "foreign_key"} and isinstance(default, str))
            or (field_type == "integer" and type(default) is int)
            or (field_type == "number" and type(default) in {int, float} and math.isfinite(default))
            or (field_type == "boolean" and isinstance(default, bool))
            or (field_type == "json" and type(default) in {str, int, float, bool})
        )
        if not valid_default:
            errors.append(f"default does not match field type: {entity_key}.{name}")
    # Malformed values must produce diagnostics rather than comparison errors.
    if errors:
        return errors
    active = {key for key, value in properties.items() if value is not None and value is not False}
    if active & {"min_length", "max_length"} and field_type != "string":
        errors.append(f"string property used on non-string field: {entity_key}.{name}")
    if active & {"minimum", "maximum"} and field_type not in {"integer", "number"}:
        errors.append(f"numeric property used on non-numeric field: {entity_key}.{name}")
    if "enum" in active and field_type != "string":
        errors.append(f"enum property used on non-string field: {entity_key}.{name}")
    if properties.get("min_length") is not None and properties.get("max_length") is not None:
        if properties["min_length"] > properties["max_length"]:
            errors.append(f"invalid length range: {entity_key}.{name}")
    if properties.get("minimum") is not None and properties.get("maximum") is not None:
        if properties["minimum"] > properties["maximum"]:
            errors.append(f"invalid numeric range: {entity_key}.{name}")
    if isinstance(default, str):
        if properties.get("min_length") is not None and len(default) < properties["min_length"]:
            errors.append(f"default is shorter than min_length: {entity_key}.{name}")
        if properties.get("max_length") is not None and len(default) > properties["max_length"]:
            errors.append(f"default is longer than max_length: {entity_key}.{name}")
        if enum is not None and default not in enum:
            errors.append(f"default is outside enum: {entity_key}.{name}")
    if type(default) in {int, float}:
        if properties.get("minimum") is not None and default < properties["minimum"]:
            errors.append(f"default is below minimum: {entity_key}.{name}")
        if properties.get("maximum") is not None and default > properties["maximum"]:
            errors.append(f"default is above maximum: {entity_key}.{name}")
    if default is not None and not logical_literal_valid(field_type, default):
        errors.append(f"default is not a valid logical value: {entity_key}.{name}")
    return errors

def logical_literal_valid(kind: str, value: Any) -> bool:
    """Fixed logical value contracts also used for schema defaults."""
    if kind != "json" and isinstance(value, str) and "\x00" in value:
        return False
    if type(value) in {int, float} and not math.isfinite(value):
        return False
    if kind == "uuid":
        return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value) is not None
    if kind not in {"date", "datetime"}:
        return True
    try:
        if kind == "date":
            return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})", value
        ):
            return False
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


# Keep the established import identity for callers and serialized references.
_field_property_errors.__module__ = 'compiler.database_design.validation'
logical_literal_valid.__module__ = 'compiler.database_design.validation'
