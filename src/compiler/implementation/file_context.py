"""File selection shared by model consumers; reasons stay in compiler logs."""
from __future__ import annotations

from typing import Any


def direct_dependencies(
    targets: list[dict[str, Any]], candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    owned = {row["module_id"] for row in targets}
    referenced = {key for row in targets for key in row.get("callees", [])} - owned
    return [row for row in candidates if row["module_id"] in referenced]


def requirement_dependencies(
    targets: list[dict[str, Any]], candidates: list[dict[str, Any]],
    requirement_id: str, frontend_ir: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Scope component callees to the requirement's UI/behavior, not all of App."""
    if frontend_ir is not None and frontend_ir.get("ir_kind") == "frontend_design":
        ui = {row["id"]: row for row in frontend_ir.get("ui", [])}
        functions = {row["id"]: row for row in frontend_ir.get("functions", [])}
        ui_ids = {key for key, row in ui.items() if requirement_id in row.get("requirement_ids", [])}
        pending = [key for key, row in functions.items() if requirement_id in row.get("requirement_ids", [])]
        for event in frontend_ir.get("events", []):
            if event.get("ui_id") in ui_ids or requirement_id in event.get("requirement_ids", []):
                pending.extend(call["target_id"] for call in event.get("handlers", []))
        modules, seen = set(), set()
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            for call in functions.get(key, {}).get("calls", []):
                if call.get("kind") == "API":
                    modules.add(call["target_id"])
                else:
                    pending.append(call["target_id"])
        target_ids = {row["module_id"] for row in targets}
        modules.update(key for row in targets for key in row.get("callees", []))
        return [row for row in candidates if row["module_id"] in modules - target_ids]
    return direct_dependencies(targets, candidates)


def file_selection_log(requirement_id: str, phase: str, files: dict[str, str], reason: str) -> str:
    return (f"FILE_CONTEXT requirement={requirement_id} phase={phase} reason={reason} "
            f"files={sorted(files)} chars={sum(len(source) for source in files.values())}")
