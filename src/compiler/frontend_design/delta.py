"""Materialize small UI deltas without asking the model to repeat full records."""
from __future__ import annotations

import copy
from typing import Any

from .contracts import patch_schema
from compiler.backend_design.validation import shape_errors


def materialize_ui_updates(ir: dict[str, Any], phase: str, output: dict[str, Any],
                           loaded: set[str], requirement_ids: list[str]) -> dict[str, Any]:
    errors = shape_errors(output, patch_schema(phase))
    if errors:
        raise ValueError("Invalid frontend delta: " + "; ".join(errors))
    result = copy.deepcopy(output)
    # Expand only inside the compiler; the model returns phase-relevant fields.
    result.setdefault("deletes", {})
    result.setdefault("load_ui_ids", [])
    result.setdefault("upserts", {}).setdefault("ui", [])
    result["deletes"].setdefault("ui", [])
    by_id = {row["id"]: row for row in ir["ui"]}
    new = result["upserts"]["ui"]
    if len({row["id"] for row in new}) != len(new):
        raise ValueError("New UI IDs must be unique")
    if any(row["id"] in by_id for row in new):
        raise ValueError("Existing UI must use ui_updates, not complete upserts")
    if phase != "ui" and (new or result["deletes"]["ui"]):
        raise ValueError("Only UI phase creates or deletes UI nodes")
    if any(row["events"] for row in new):
        raise ValueError("New UI events must be designed in interaction phase")
    result["changed_event_ids"] = []
    seen = {row["id"] for row in new} | set(result["deletes"]["ui"])
    for update in result["ui_updates"]:
        key = update["id"]
        if key not in loaded or key not in by_id or key in seen:
            raise ValueError(f"UI update must target a unique loaded existing node: {key}")
        seen.add(key)
        if phase != "ui" and "children" in update:
            raise ValueError("Only UI phase changes containment")
        if phase != "interaction" and "events" in update:
            raise ValueError("Only interaction phase changes events")
        changes = update.get("set", {})
        if phase == "data" and set(changes) - {"repeat"}:
            raise ValueError("Data phase updates UI bindings and repeat only")
        if phase == "interaction" and set(changes) - {"spec"}:
            raise ValueError("Interaction phase updates UI behavioral spec only")
        if phase != "ui" and set(update.get("unset", [])) - ({"repeat"} if phase == "data" else set()):
            raise ValueError("Cannot remove UI presentation fields outside UI phase")
        row = copy.deepcopy(by_id[key])
        row.update(changes)
        for field in update.get("unset", []):
            if field in changes:
                raise ValueError(f"Cannot set and unset the same UI field: {field}")
            row.pop(field, None)
        if "bindings" in update:
            bindings = update["bindings"]
            for field in bindings["remove"]:
                row["bindings"].pop(field, None)
            row["bindings"].update(bindings["set"])
        if "children" in update:
            children = update["children"]
            unknown = set(children["remove"]) - set(row["children"])
            if unknown:
                raise ValueError(f"Cannot remove unknown children: {sorted(unknown)}")
            row["children"] = [child for child in row["children"] if child not in children["remove"]]
            for addition in children["add"]:
                child, before = addition["id"], addition.get("before_id")
                if child in row["children"] or before is not None and before not in row["children"]:
                    raise ValueError(f"Invalid child insertion: {addition}")
                index = row["children"].index(before) if before is not None else len(row["children"])
                row["children"].insert(index, child)
        if "events" in update:
            delta = update["events"]
            events = {event["id"]: event for event in row["events"]}
            for event_id in delta["deletes"]:
                if event_id not in events:
                    raise ValueError(f"Cannot delete unknown event: {event_id}")
                if set(events[event_id].get("requirement_ids", [])) - set(requirement_ids):
                    raise ValueError(f"Cannot delete another requirement's event: {event_id}")
                events.pop(event_id)
            for event in delta["upserts"]:
                events[event["id"]] = event
                result["changed_event_ids"].append(event["id"])
            row["events"] = list(events.values())
        new.append(row)
    return result
