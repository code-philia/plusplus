"""Temporary skeleton-lowering view; never persisted as frontend design IR."""
from __future__ import annotations

import copy
import json
from typing import Any


def project_skeleton_ir(ir: dict[str, Any], backend: dict[str, Any]) -> dict[str, Any]:
    """Keep the existing placeholder lowerer usable until native UI lowering exists.

    One compiler-chosen App hosts the blueprint. This is not component discovery
    or a behavioral implementation, and never changes the authoritative IR.
    """
    if ir.get("ir_kind") != "frontend_design":
        return ir
    backend = backend.get("backend_blueprint", backend)
    registry = {row["id"]: row for row in [*backend.get("types", []), *ir.get("types", [])]}
    cid = "lowering.App"

    def value_type(value: dict[str, Any], seen: frozenset[str] = frozenset()) -> dict[str, Any]:
        kind = value["kind"]
        if kind == "scalar":
            name = value["name"]
            if name == "json":
                return {"kind": "object", "fields": []}
            return {"kind": name if name in {"string", "number", "integer", "boolean"} else "string"}
        if kind == "array":
            return {"kind": "array", "items": value_type(value["items"], seen)}
        key = value["type_id"]
        definition = registry.get(key)
        if not definition or key in seen:
            return {"kind": "object", "fields": []}
        if definition["kind"] == "enum":
            return {"kind": "string"}
        return {"kind": "object", "fields": [
            {"name": field["name"], "required": field["required"],
             "type": nullable_type(field["type"], field.get("nullable", False), seen | {key})}
            for field in definition["fields"]]}

    def nullable_type(value: dict[str, Any], nullable: bool, seen: frozenset[str] = frozenset()) -> dict[str, Any]:
        result = value_type(value, seen)
        return {"kind": "union", "variants": [result, {"kind": "null"}]} if nullable else result

    requirements = list(dict.fromkeys(rid for table in ("ui", "data", "events", "functions")
                                     for row in ir[table] for rid in row.get("requirement_ids", [])))
    result = {"root_component_id": cid, "components": [{
        "id": cid, "name": "AppUI", "spec": "Implement the technology-neutral UI tree and interactions.",
        "requirement_ids": requirements, "ui_root_id": ir["root_ui_id"],
    }], "data": [], "properties": [], "ui": [], "events": [], "handlers": [], "effects": [], "api_dependencies": []}
    for row in ir["data"]:
        result["properties"].append({**copy.deepcopy(row), "component_id": cid, "kind": "STATE",
            "type": nullable_type(row["type"], row["nullable"]),
            "initial": {"kind": "LITERAL", "value": row["initial"]} if "initial" in row else None,
            "derive": None, "blueprint": copy.deepcopy(row)})
    for row in ir["ui"]:
        result["ui"].append({**copy.deepcopy(row), "name": row["id"], "component_id": cid,
            "kind": "FRAGMENT", "element": None, "component_ref": None, "slot_data_id": None,
            "attributes": [], "text": None, "condition": None, "repeat": None,
            "arguments": [], "callbacks": [], "blueprint": copy.deepcopy(row),
            "spec": row["spec"] + "\nUI blueprint: " + json.dumps(row, ensure_ascii=False)})

    def interface(owner: str, fields: list[dict[str, Any]], direction: str) -> None:
        for index, field in enumerate(fields):
            result["data"].append({"id": f"lowering.{owner}.{direction}.{index}",
                "name": field["name"], "spec": field.get("description", ""),
                "semantic_id": field["semantic_id"], "owner_id": owner, "direction": direction,
                "required": field["required"], "type": nullable_type(field["type"], field["nullable"]),
                "default": None, "requirement_ids": []})

    for row in ir["functions"]:
        result["handlers"].append({**copy.deepcopy(row), "name": row["id"], "component_id": cid,
            "invokes": [], "emits": [], "blueprint": copy.deepcopy(row),
            "spec": row["spec"] + "\nFunction blueprint: " + json.dumps(row, ensure_ascii=False)})
        interface(row["id"], row["inputs"], "INPUT")
        interface(row["id"], row["outputs"], "OUTPUT")
        result["api_dependencies"].extend({"consumer_id": cid, "api_id": call["target_id"], "bindings": []}
                                           for call in row["calls"] if call["kind"] == "API")
    for row in ir["events"]:
        result["events"].append({**copy.deepcopy(row), "component_id": cid, "kind": "UI",
            "event_name": row["name"], "handler_id": None, "arguments": [], "blueprint": copy.deepcopy(row),
            "spec": row["spec"] + "\nEvent blueprint: " + json.dumps(row, ensure_ascii=False)})
        interface(row["id"], row["payload"], "OUTPUT")
    return result
