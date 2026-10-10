"""Plan bounded semantic UI subtrees and separate frontend function files."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from compiler.ir.frontend import frontend_events


SEMANTIC_BOUNDARIES = {"screen", "form", "navigation", "dialog", "tabs", "table"}
CONTAINERS = SEMANTIC_BOUNDARIES | {"group", "section", "list", "field", "row"}
MAX_DEPTH = 3
TARGET_NODES = 24
REGION_NODES = 8


def _pascal(value: str) -> str:
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|[0-9]+", value)
    name = "".join(word[:1].upper() + word[1:] for word in words)
    return "Ui" + name if not name or name[0].isdigit() else name


def _allocate(bases: dict[str, str], reserved: set[str] | None = None) -> dict[str, str]:
    counts = Counter(name.casefold() for name in bases.values())
    occupied = {name.casefold() for name in reserved or set()}
    result = {}
    for key, base in sorted(bases.items()):
        name = base
        if counts[name.casefold()] > 1 or name.casefold() in occupied:
            name += hashlib.sha256(key.encode()).hexdigest()[:8]
        while name.casefold() in occupied:
            name += "View"
        result[key] = name
        occupied.add(name.casefold())
    return result


def plan_ui_units(ir: dict[str, Any]) -> dict[str, Any]:
    ui = {row["id"]: row for row in ir["ui"]}
    root = ir["root_ui_id"]
    order, seen, parents = [], set(), {}
    pending = [root]
    while pending:
        key = pending.pop()
        if key in seen or key not in ui:
            raise ValueError(f"UI must be a rooted tree; invalid node: {key}")
        seen.add(key)
        order.append(key)
        for child in reversed(ui[key]["children"]):
            if child in parents or child == root:
                raise ValueError(f"UI has multiple parents or a cycle: {child}")
            parents[child] = key
            pending.append(child)
    if set(ui) != seen or len(ui) != len(ir["ui"]):
        raise ValueError("UI tree contains unattached or duplicate nodes")
    sizes = {}
    for key in reversed(order):
        sizes[key] = 1 + sum(sizes[child] for child in ui[key]["children"])
    units, membership, depths = {}, {}, {}
    for key in order:
        row = ui[key]
        parent = parents.get(key)
        owner = membership[parent] if parent else root
        depth = depths[parent] + 1 if parent else 0
        reason = None
        if key == root:
            reason = "application_root"
        elif row["kind"] in SEMANTIC_BOUNDARIES:
            reason = "semantic_" + row["kind"]
        elif row.get("repeat"):
            reason = "repeated_region"
        elif row["kind"] in CONTAINERS and row["children"]:
            if row["kind"] in {"group", "section", "list"} and sizes[key] >= REGION_NODES and (
                row["kind"] == "section" or depth >= 2 and len(row["children"]) > 1
            ):
                reason = "semantic_region"
            elif depth >= MAX_DEPTH:
                reason = "depth_limit"
            elif len(units[owner]["ui_ids"]) + sizes[key] > TARGET_NODES and sizes[key] >= 4 and len(row["children"]) > 1:
                reason = "size_limit"
        if reason:
            owner, depth = key, 0
            units[owner] = {"id": owner, "root_ui_id": owner, "ui_ids": [],
                            "boundary_reason": reason, "child_component_ids": []}
            if parent:
                units[membership[parent]]["child_component_ids"].append(owner)
        membership[key], depths[key] = owner, depth
        units[owner]["ui_ids"].append(key)
    bases = {}
    for key in units:
        name = "ApplicationShell" if key == root else _pascal(ui[key]["name"])
        if ui[key]["kind"] == "screen" and not name.endswith("Page"):
            name += "Page"
        bases[key] = name
    names = _allocate(bases, {"App", "React", "JSONValue", "NotImplementedError"})
    for key, unit in units.items():
        folder = "pages" if ui[key]["kind"] == "screen" else "components"
        unit.update(symbol=names[key], file=f"frontend/src/{folder}/{names[key]}.tsx",
                    max_relative_depth=max(depths[item] for item in unit["ui_ids"]),
                    node_count=len(unit["ui_ids"]))
    return {"rules": {"semantic_boundaries": sorted(SEMANTIC_BOUNDARIES),
                       "max_container_depth": MAX_DEPTH, "target_nodes": TARGET_NODES,
                       "region_nodes": REGION_NODES,
                       "atomic_controls": "Keep leaves together; node target is soft for wide atomic regions"},
            "units": list(units.values()), "ui_units": membership,
            "ui_files": {key: units[owner]["file"] for key, owner in membership.items()},
            "ui_symbols": {key: names[owner] for key, owner in membership.items()}}


def plan_behavior(ir: dict[str, Any], ui_plan: dict[str, Any]) -> dict[str, Any]:
    functions = {row["id"]: row for row in ir["functions"]}
    events = frontend_events(ir)
    bases = {}
    for row in [*functions.values(), *events]:
        value = row.get("name") if row["id"] in functions else None
        if not value:
            value = re.split(r"(?:^|[.:])(?:fn|function|event)[.:]", row["id"])[-1]
        stem = _pascal(value)
        bases[row["id"]] = ("run" if row["id"] in functions else "handle") + stem
    symbols = _allocate(bases, set(ui_plan["ui_symbols"].values()))
    function_files = {key: f"frontend/src/functions/{symbols[key]}.ts" for key in functions}
    event_files = {row["id"]: ui_plan["ui_files"][row["ui_id"]] for row in events}
    users: dict[str, set[str]] = {key: set() for key in functions}
    for event in events:
        pending, seen = [call["target_id"] for call in event["handlers"]], set()
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            if key not in functions:
                raise ValueError(f"Unknown frontend function: {key}")
            users[key].add(event_files[event["id"]])
            pending.extend(call["target_id"] for call in functions[key]["calls"] if call["kind"] == "FUNC")
    for unit in ui_plan["units"]:
        unit["event_ids"] = sorted(key for key, path in event_files.items() if path == unit["file"])
    return {"event_files": event_files, "function_files": function_files,
            "behavior_symbols": symbols,
            "function_users": {key: sorted(paths) for key, paths in users.items()}}


def api_symbols(apis: dict[str, Any]) -> dict[str, str]:
    bases = {}
    for key, api in apis.items():
        stem = _pascal(str(api.get("name") or key.rsplit(".", 1)[-1]))
        bases[key] = stem[:1].lower() + stem[1:] + "Api"
    return _allocate(bases)
