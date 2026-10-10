"""Stable TypeScript names and lossless frontend/backend value types."""
from __future__ import annotations

from compiler.ir.backend import module_fields

import hashlib
import json
import re
from typing import Any


def js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def symbol(prefix: str, identity: str) -> str:
    label = re.sub(r"[^A-Za-z0-9]", "_", identity)[-48:]
    return prefix + "_" + label + "_" + hashlib.sha256(identity.encode()).hexdigest()[:10]


class Types:
    def __init__(self, ir: dict[str, Any], backend: dict[str, Any]) -> None:
        self.ir = ir
        self.backend = backend
        self.definitions = {row["id"]: row for row in [*self.backend.get("types", []), *ir["types"]]}
        self.apis = {row["id"]: {**row, "inputs": module_fields(row, "inputs"),
                                "outputs": module_fields(row, "outputs")}
                     for row in self.backend.get("modules", []) if row.get("kind") == "API"}

    def value(self, value: Any, nullable: bool = False) -> str:
        if isinstance(value, str):
            result = {"integer": "number", "number": "number", "boolean": "boolean", "json": "JSONValue"}.get(value, "string")
        elif value["kind"] == "scalar":
            result = self.value(value["name"])
        elif value["kind"] == "array":
            result = f"Array<{self.value(value['items'])}>"
        elif value["kind"] == "named":
            result = symbol("Type", value["type_id"])
        elif value["kind"] == "enum":
            result = " | ".join(js(v) for v in value["values"]) or "never"
        elif value["kind"] == "object":
            result = self.fields(value["fields"], semantic=False)
        else:
            raise ValueError(f"Unsupported frontend value type: {value}")
        return f"({result}) | null" if nullable else result

    def fields(self, fields: list[dict[str, Any]], semantic: bool = True) -> str:
        return "{\n" + "\n".join(
            js(field["semantic_id"] if semantic else field["name"]) +
            ("" if field["required"] else "?") + ": " +
            self.value(field["type"], field.get("nullable", False)) + ";"
            for field in fields) + "\n}"
