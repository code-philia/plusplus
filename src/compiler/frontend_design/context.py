"""Bounded reuse retrieval and derived frontend traceability."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

TABLES = ("ui", "data", "events", "functions", "types")


def ranked(rows: list[dict[str, Any]], query: Any, count: int) -> list[dict[str, Any]]:
    def terms(value: Any) -> set[str]:
        return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", json.dumps(value, ensure_ascii=False).lower()))
    wanted = terms(query)
    return sorted(rows, key=lambda row: (-len(wanted & terms(row)), row["id"]))[:count]


def value_references(value: Any, kind: str) -> set[str]:
    if isinstance(value, list):
        return {key for child in value for key in value_references(child, kind)}
    if not isinstance(value, dict) or value.get("kind") == "literal":
        return set()
    own = {value["id"]} if value.get("kind") == kind and "id" in value else set()
    return own | {key for child in value.values() for key in value_references(child, kind)}


def ui_data_associations(ir: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    ui = {row["id"]: row for row in ir.get("ui", [])}
    result = {}
    for row in ui.values():
        direct = value_references(row.get("bindings", {}), "data")
        sources = value_references(row.get("bindings", {}), "item")
        sources |= value_references(row.get("repeat", {}), "item")
        data = direct | value_references(row.get("repeat", {}), "data")
        pending, seen = list(sources), set()
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            repeat = ui.get(key, {}).get("repeat", {})
            data |= value_references(repeat, "data")
            pending.extend(value_references(repeat, "item") - seen)
        result[row["id"]] = {"direct_ids": sorted(direct), "data_ids": sorted(data),
                              "repeat_ui_ids": sorted(seen)}
    return result


def frontend_ir_traceability(ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    links: dict[str, dict[str, Any]] = {}
    for table, name in (("ui", "ui_ids"), ("data", "data_ids"),
                        ("events", "event_ids"), ("functions", "function_ids")):
        for row in ir.get(table, []):
            for rid in row.get("requirement_ids", []):
                link = links.setdefault(rid, {"ui_scope": "UI_AFFECTING"})
                link.setdefault(name, []).append(row["id"])
                if table in {"ui", "events"}:
                    link["ui_scope"] = "UI_REQUIRED"
                if table == "ui":
                    link.setdefault("visual_reference_ids", []).extend(row.get("visual_reference_ids", []))
                if table == "functions":
                    link.setdefault("api_ids", []).extend(call["target_id"] for call in row.get("calls", [])
                                                           if call.get("kind") == "API")
    for row in links.values():
        for key, value in row.items():
            if isinstance(value, list):
                row[key] = list(dict.fromkeys(value))
    return links


def local_context(ir: dict[str, Any], rid: str, requirement: dict[str, Any],
                  related_ids: set[str], *, include_related: bool = False) -> dict[str, Any]:
    rows = {row["id"]: row for table in TABLES for row in ir[table]}
    ui = {row["id"]: row for row in ir["ui"]}
    parents = {child: row["id"] for row in ui.values() for child in row.get("children", [])}
    owners = related_ids | {rid} if include_related else {rid}
    owned = {key for key, row in rows.items() if owners & set(row.get("requirement_ids", []))}
    related = [row for row in ui.values() if related_ids & set(row.get("requirement_ids", []))]
    candidates = ranked(related, requirement, 8)
    candidates += ranked(list(ui.values()), requirement, 12)
    shared_functions = ranked(ir["functions"], requirement, 8)
    shared_data = ranked(ir["data"], requirement, 8)
    selected = owned | {ir["root_ui_id"]}
    selected.update(row["id"] for row in [*candidates, *shared_functions, *shared_data])
    # Fetch candidate subtrees to make reuse a real option, with a bounded expansion.
    pending = [row["id"] for row in candidates]
    expanded: set[str] = set()
    while pending and len(expanded) < 48:
        key = pending.pop(0)
        if key in expanded or key not in ui:
            continue
        expanded.add(key)
        selected.add(key)
        pending.extend(ui[key].get("children", []))
    # Ancestors are editable full records: attaching/moving UI needs both parents.
    for key in list(selected):
        seen = set()
        while key in parents and key not in seen:
            seen.add(key)
            key = parents[key]
            selected.add(key)
    # Full immediate children preserve sibling identities during parent replacement.
    for key in list(selected):
        selected.update(ui.get(key, {}).get("children", []))
    for event in ir["events"]:
        if event.get("ui_id") in selected:
            selected.add(event["id"])
    # Include function/data dependencies; no recursive expansion of shared UI containers.
    pending = list(selected)
    visited = set()
    while pending:
        key = pending.pop()
        if key in visited or key not in rows:
            continue
        visited.add(key)
        row = rows[key]
        references = value_references(row, "data")
        references.update(row.get("reads", []))
        references.update(row.get("writes", []))
        references.update(call["target_id"] for call in [*row.get("calls", []), *row.get("handlers", [])]
                          if call.get("kind") == "FUNC")
        fresh = references - selected
        selected.update(fresh)
        pending.extend(fresh)
    records = {table: [copy.deepcopy(row) for row in ir[table] if row["id"] in selected]
               for table in TABLES if table != "types"}
    # Compact catalogs expose the rest of the system without sending every full subtree.
    return {
        "root_ui_id": ir["root_ui_id"], "existing": records,
        "ui_catalog": [{"id": row["id"], "kind": row["kind"],
                        "spec": row.get("spec", "")[:180], "parent_id": parents.get(row["id"]),
                        "requirement_ids": row.get("requirement_ids", [])}
                       for row in ranked(list(ui.values()), requirement, 24)],
        "function_catalog": [{"id": row["id"], "spec": row.get("spec", "")[:180]}
                             for row in shared_functions],
    }


def required_types(values: Any, definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    registry = {row["id"]: row for row in definitions}

    def names(value: Any) -> set[str]:
        if isinstance(value, list):
            return {key for child in value for key in names(child)}
        if not isinstance(value, dict) or value.get("kind") == "literal":
            return set()
        own = {value["type_id"]} if value.get("kind") == "named" else set()
        return own | {key for child in value.values() for key in names(child)}

    pending, found = list(names(values)), set()
    while pending:
        key = pending.pop()
        if key in found:
            continue
        found.add(key)
        pending.extend(names(registry.get(key, {})) - found)
    return [copy.deepcopy(row) for row in definitions if row["id"] in found]
