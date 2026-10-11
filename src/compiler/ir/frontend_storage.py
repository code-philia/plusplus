"""Nested JSON storage with a lossless adapter to the compiler's UI index."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from utils.jsonio import write_json_atomic


def nested_frontend_ir(ir: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(ir)
    rows = ir["ui"]
    ui = {row["id"]: row for row in rows}
    if len(ui) != len(rows):
        raise ValueError("UI IDs must be unique")
    seen: set[str] = set()

    def node(key: str) -> dict[str, Any]:
        if key not in ui or key in seen:
            raise ValueError(f"Invalid UI tree edge: {key}")
        seen.add(key)
        row = copy.deepcopy(ui[key])
        row["children"] = [node(child) for child in row["children"]]
        return row

    result["ui"] = [node(ir["root_ui_id"])]
    if seen != ui.keys():
        raise ValueError("UI tree contains unattached nodes")
    return result


def indexed_frontend_ir(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    ancestors: set[int] = set()

    def visit(source: dict[str, Any]) -> str:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str):
            raise ValueError("Nested UI children must be node records with IDs")
        key = source["id"]
        if key in seen or id(source) in ancestors:
            raise ValueError(f"Repeated or cyclic UI node: {key}")
        seen.add(key)
        ancestors.add(id(source))
        if not isinstance(source.get("children"), list):
            raise ValueError(f"UI children must be a list: {key}")
        row = copy.deepcopy({name: value for name, value in source.items() if name != "children"})
        rows.append(row)
        row["children"] = [visit(child) for child in source["children"]]
        ancestors.remove(id(source))
        return key

    roots = document.get("ui")
    if not isinstance(roots, list) or len(roots) != 1:
        raise ValueError("Frontend JSON must contain exactly one UI root")
    if visit(roots[0]) != document.get("root_ui_id"):
        raise ValueError("Nested UI root must match root_ui_id")
    result["ui"] = rows
    return result


def read_frontend_ir(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected a Frontend IR mapping: {path}")
    return indexed_frontend_ir(document)


def write_frontend_ir(path: Path, ir: dict[str, Any]) -> None:
    document = nested_frontend_ir(ir)
    write_json_atomic(path, document)
    write_json_atomic(path.with_name("ui.json"), {"ui": document["ui"]})
