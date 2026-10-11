"""Bounded reuse retrieval and derived frontend traceability."""
from __future__ import annotations

import copy
import json
import posixpath
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


def visual_screen_matches(ir: dict[str, Any], rid: str,
                          requirements: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Index requirement image paths to existing screens without loading images."""
    ui = {row["id"]: row for row in ir["ui"]}
    parents = {child: row["id"] for row in ui.values() for child in row.get("children", [])}
    def normalize(path: str) -> str:
        return posixpath.normpath(path.replace(chr(92), "/"))

    owners_by_path: dict[str, set[str]] = {}
    for owner, row in requirements.items():
        for path in row.get("visual_references", []):
            owners_by_path.setdefault(normalize(path), set()).add(owner)
    references = {normalize(path) for path in requirements.get(rid, {}).get("visual_references", [])}
    events = frontend_events(ir)

    def screen_for(key: str) -> str | None:
        seen = set()
        while key in ui and key not in seen:
            seen.add(key)
            if ui[key]["kind"] == "screen":
                return key
            key = parents.get(key)
        return None

    result = []
    for reference in sorted(references):
        owners = owners_by_path[reference]
        anchors = {row["id"] for row in ui.values() if owners & set(row.get("requirement_ids", []))}
        linked = {key for owner in owners for field in ("implements", "uses")
                  for key in ir.get("requirement_links", {}).get(owner, {}).get(field, [])}
        anchors.update(linked & ui.keys())
        anchors.update(event["ui_id"] for event in events if event["id"] in linked
                       or owners & set(event.get("requirement_ids", [])))
        screens = sorted({screen for key in anchors if (screen := screen_for(key)) is not None})
        for screen in screens:
            result.append({"screen_id": screen, "visual_reference_path": reference,
                           "reason": "shared_visual_path", "association": "requirement_reference",
                           "ambiguous": len(screens) > 1,
                           "matched_ui_ids": sorted(key for key in anchors if screen_for(key) == screen),
                           "matched_requirement_ids": sorted(owners)})
    return result


def local_context(ir: dict[str, Any], rid: str, requirement: dict[str, Any],
                  related_ids: set[str], requested_ui_ids: set[str] | None = None,
                  max_chars: int = 48000, *,
                  screen_matches: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Load requirement pages only; expose other pages through a tree summary."""
    rows = {row["id"]: row for table in TABLES for row in ir[table]}
    events = frontend_events(ir)
    rows.update({event["id"]: event for event in events})
    ui = {row["id"]: row for row in ir["ui"]}
    parents = {child: row["id"] for row in ui.values() for child in row["children"]}
    owned = {key for key, row in rows.items() if rid in row.get("requirement_ids", [])}
    used = set(ir.get("requirement_links", {}).get(rid, {}).get("uses", []))
    requested = (requested_ui_ids or set()) & ui.keys()
    matches = screen_matches or []
    screens = {row["screen_id"] for row in matches}
    anchors = (owned | used | requested) & ui.keys()
    anchors.update(event["ui_id"] for event in events if event["id"] in owned | used)
    scoped_data = (owned | used) & {row["id"] for row in ir["data"]}
    scoped_data.update(key for row in ir["functions"] if row["id"] in owned | used
                       for key in [*row.get("reads", []), *row.get("writes", [])])
    anchors.update(row["scope_ui_id"] for row in ir["data"] if row["id"] in scoped_data
                   and row["scope_ui_id"] in ui)
    for key in anchors:
        seen = set()
        while key in ui and key not in seen:
            seen.add(key)
            if ui[key]["kind"] == "screen":
                screens.add(key)
                break
            key = parents.get(key)
    region = set(anchors)
    pending, walked = sorted(screens), set()
    while pending:
        key = pending.pop()
        if key in walked or key not in ui:
            continue
        walked.add(key)
        region.add(key)
        pending.extend(child for child in ui[key]["children"] if child not in walked
                       and ui[child]["kind"] != "screen")
    # The root is needed for attaching a new page, never for loading all pages.
    selected = owned | (used & rows.keys()) | region | requested | {ir["root_ui_id"]}
    selected.update(row["id"] for row in ir["data"] if row["scope_ui_id"] in region)
    selected.update(event["id"] for event in events if event["ui_id"] in selected)
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
    structural = {ir["root_ui_id"]}
    for key in selected & ui.keys():
        seen = set()
        while key in parents and key not in seen:
            seen.add(key)
            key = parents[key]
            if key not in selected:
                structural.add(key)
    records = {table: [] for table in ("ui", "data", "functions")}
    size, loaded = 0, set()
    candidates = [(table, row) for table in records for row in ir[table] if row["id"] in selected]
    candidates.sort(key=lambda pair: (pair[1]["id"] not in requested,
                                      pair[1]["id"] not in screens | {ir["root_ui_id"]},
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
    loaded_ui = loaded & ui.keys()

    def tree(key: str, summary: bool) -> dict[str, Any]:
        source = ui[key]
        if summary:
            row = {name: copy.deepcopy(source[name]) for name in ("id", "name", "kind", "route") if name in source}
            row["spec"] = source.get("spec", "")[:120]
            children = source["children"]
        else:
            row = copy.deepcopy(source)
            children = [child for child in source["children"] if child in loaded_ui]
        row["children"] = [tree(child, summary) for child in children]
        return row

    # Nested page records show actual containment; the separate summary retains
    # unloaded edges. Edits use stable IDs, never reconstruct a partial subtree.
    roots = [row["id"] for row in records["ui"] if parents.get(row["id"]) not in loaded_ui]
    records["ui"] = [tree(key, False) for key in roots]
    return {
        "root_ui_id": ir["root_ui_id"], "existing": records,
        "loaded_ui_ids": sorted(loaded_ui), "page_ids": sorted(screens),
        "page_context_mode": "EXISTING_PAGE" if screens else "NEW_PAGE_OR_SELECT_EXISTING",
        "ui_tree_summary": tree(ir["root_ui_id"], True),
        "structural_ui": [ui_summary(ui[key], parents) for key in sorted(structural - loaded)],
        "requirement_links": copy.deepcopy(ir.get("requirement_links", {}).get(rid, {"implements": [], "uses": []})),
        "preferred_screen_matches": copy.deepcopy(matches),
        "omitted_ids": sorted((selected & rows.keys()) - loaded - {event["id"] for event in events}),
        "context_budget_chars": max_chars,
        "function_catalog": [{"id": row["id"], "spec": row.get("spec", "")[:180]}
                             for row in ranked(ir["functions"], requirement, 4)],
    }


def context_ui_nodes(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Iterate full records in the nested model context, excluding summaries."""
    result, pending = [], list(context["existing"]["ui"])
    while pending:
        row = pending.pop()
        result.append(row)
        pending.extend(row["children"])
    return result


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
