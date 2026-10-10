"""Bounded reuse retrieval and derived frontend traceability."""
from __future__ import annotations

import copy
import json
import re

from compiler.ir.frontend import frontend_events
from typing import Any

TABLES = ("ui", "data", "functions", "types")


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
        for row in (frontend_events(ir) if table == "events" else ir.get(table, [])):
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
    for rid, association in ir.get("requirement_links", {}).items():
        link = links.setdefault(rid, {"ui_scope": "NO_UI"})
        link["implements"] = list(association.get("implements", []))
        link["uses"] = list(association.get("uses", []))
        ui_ids = {row['id'] for row in ir.get('ui', [])}
        if link['ui_scope'] == 'NO_UI' and ui_ids & set(link['uses']):
            link['ui_scope'] = 'UI_AFFECTING'
    for row in links.values():
        for key, value in row.items():
            if isinstance(value, list):
                row[key] = list(dict.fromkeys(value))
    return links


def local_context(ir: dict[str, Any], rid: str, requirement: dict[str, Any],
                  related_ids: set[str], requested_ui_ids: set[str] | None = None,
                  max_chars: int = 48000) -> dict[str, Any]:
    rows = {row["id"]: row for table in TABLES for row in ir[table]}
    events = frontend_events(ir)
    rows.update({event["id"]: event for event in events})
    ui = {row["id"]: row for row in ir["ui"]}
    parents = {child: row["id"] for row in ui.values() for child in row.get("children", [])}
    # Direct ownership is never inherited through a requirement or UI ancestor.
    owned = {key for key, row in rows.items() if rid in row.get("requirement_ids", [])}
    used = set(ir.get("requirement_links", {}).get(rid, {}).get("uses", []))
    related = [row for row in ui.values() if related_ids & set(row.get("requirement_ids", []))
               and row["id"] != ir["root_ui_id"]]
    candidates = ranked(related, requirement, 4)
    candidates += ranked([row for row in ui.values() if row["id"] != ir["root_ui_id"]], requirement, 8)
    shared_functions = ranked(ir["functions"], requirement, 4)
    shared_data = ranked(ir["data"], requirement, 4)
    requested = (requested_ui_ids or set()) & ui.keys()
    if len(ui) <= 64 and sum(len(json.dumps(row, ensure_ascii=False)) for row in ui.values()) <= 24000:
        requested = set(ui)
    selected = owned | (used & rows.keys()) | requested
    selected.update(event["ui_id"] for event in events if event["id"] in selected)
    selected.update(row["id"] for row in [*candidates, *shared_functions, *shared_data])
    # Candidate screen/group records expose children IDs, never all sibling features.
    # Expand a small non-screen region only; owned controls are already complete.
    pending = [row["id"] for row in candidates if row.get("kind") not in {"screen", "group"}]
    expanded: set[str] = set()
    while pending and len(expanded) < 24:
        key = pending.pop(0)
        if key in expanded or key not in ui:
            continue
        expanded.add(key)
        selected.add(key)
        pending.extend(child for child in ui[key].get("children", [])
                       if ui.get(child, {}).get("kind") != "screen")
    # Events follow the selected semantic controls, not attachment ancestors.
    selected.update(event["id"] for event in events if event.get("ui_id") in selected)
    pending, visited = list(selected), set()
    while pending:
        key = pending.pop()
        if key in visited or key not in rows:
            continue
        visited.add(key)
        row = rows[key]
        references = value_references(row, "data") | value_references(row, "item")
        references.update(row.get("reads", []))
        references.update(row.get("writes", []))
        references.update(call["target_id"] for call in [*row.get("calls", []), *row.get("handlers", []),
                          *[call for event in row.get("events", []) for call in event.get("handlers", [])]]
                          if call.get("kind") == "FUNC")
        fresh = (references & rows.keys()) - selected
        selected.update(fresh)
        pending.extend(fresh)
    # Keep attachment ancestors separate. Their ordered child IDs are sufficient
    # to preserve unrelated siblings during full-record parent updates.
    structural = {ir["root_ui_id"]}
    for key in list(selected):
        seen = set()
        while key in parents and key not in seen:
            seen.add(key)
            key = parents[key]
            if key not in selected:
                structural.add(key)
    # Fit full records to a serialized context budget. Requested UI records are
    # mandatory; other candidates remain discoverable by ID when omitted.
    records = {table: [] for table in TABLES if table != "types"}
    size, loaded = 0, set()
    candidates = [(table, row) for table in records for row in ir[table]
                  if row["id"] in selected]
    candidates.sort(key=lambda pair: (pair[1]["id"] not in requested,
                                      pair[1]["id"] not in owned, pair[1]["id"]))
    for table, row in candidates:
        cost = len(json.dumps(row, ensure_ascii=False))
        if size + cost > max_chars:
            if row["id"] in requested:
                raise ValueError("Requested UI context exceeds budget; select fewer UI nodes")
            continue
        records[table].append(copy.deepcopy(row))
        loaded.add(row["id"])
        size += cost
    # Structural attachment records are compact, not permission to replace
    # unloaded behavior. Full sibling/event data is fetched before any edit.
    structural_records = [ui_summary(ui[key], parents) for key in sorted(structural - loaded)]
    return {
        "root_ui_id": ir["root_ui_id"], "existing": records,
        "structural_ui": structural_records,
        "requirement_links": copy.deepcopy(ir.get("requirement_links", {}).get(rid, {"implements": [], "uses": []})),
        "ui_catalog": [ui_summary(row, parents) for row in ranked(list(ui.values()), requirement, 24)],
        "omitted_ids": sorted((selected & rows.keys()) - loaded - {event["id"] for event in events}),
        "context_budget_chars": max_chars,
        "function_catalog": [{"id": row["id"], "spec": row.get("spec", "")[:180]}
                             for row in shared_functions],
    }


def ui_summary(row: dict[str, Any], parents: dict[str, str]) -> dict[str, Any]:
    return {"id": row["id"], "name": row.get("name", ""), "kind": row["kind"],
            "spec": row.get("spec", "")[:180], "parent_id": parents.get(row["id"]),
            "route": row.get("route"), "child_count": len(row.get("children", [])),
            "children": list(row.get("children", []))[:48],
            "children_truncated": len(row.get("children", [])) > 48,
            "requirement_ids": list(row.get("requirement_ids", []))}


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
