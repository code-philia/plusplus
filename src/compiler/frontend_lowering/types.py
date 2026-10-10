"""Stable TypeScript names and lossless frontend/backend value types."""
from __future__ import annotations

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
        self.backend = backend.get("backend_blueprint", backend)
        self.definitions = {row["id"]: row for row in [*self.backend.get("types", []), *ir["types"]]}
        self.apis = {row["id"]: row for row in self.backend.get("modules", []) if row.get("kind") == "API"}

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
            self.value(field.get("backend_type", field["type"]), field.get("nullable", False)) + ";"
            for field in fields) + "\n}"

    def source(self) -> str:
        lines = ["export type JSONValue = string | number | boolean | null | JSONValue[] | { [key: string]: JSONValue };", ""]
        for key, row in sorted(self.definitions.items()):
            value = self.fields(row["fields"], semantic=False) if row["kind"] == "object" else " | ".join(js(v) for v in row["values"]) or "never"
            lines.append(f"export type {symbol('Type', key)} = {value};")
        for table, ins, outs in (("functions", "inputs", "outputs"), ("events", "payload", None)):
            for row in self.ir[table]:
                lines.append(f"export type {symbol('Input', row['id'])} = {self.fields(row[ins])};")
                if outs:
                    lines.append(f"export type {symbol('Output', row['id'])} = {self.fields(row[outs])};")
        for key, row in self.apis.items():
            lines.append(f"export type {symbol('Input', key)} = {self.fields(row['inputs'])};")
            lines.append(f"export type {symbol('Output', key)} = {self.fields(row['outputs'])};")
        lines.append("export interface DataValues {")
        for row in self.ir["data"]:
            lines.append("// Data blueprint: " + js(row))
            lines.append(js(row["id"]) + ": " + self.value(row["type"], row["nullable"]) + (" | undefined" if "initial" not in row else "") + ";")
        lines.append("}")
        for name, rows in (("FunctionContracts", self.ir["functions"]), ("APIContracts", list(self.apis.values()))):
            lines.append(f"export interface {name} {{")
            for row in rows:
                lines.append(js(row["id"]) + ": { input: " + symbol("Input", row["id"]) + "; output: " + symbol("Output", row["id"]) + " };")
            lines.append("}")
        return "\n".join(lines) + "\n"
