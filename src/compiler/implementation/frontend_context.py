"""Read-only context selection for the canonical frontend IR."""
from __future__ import annotations

from compiler.ir.frontend import frontend_events
from compiler.ir.frontend_storage import read_frontend_ir
from compiler.preprocessing.visual_cache import cache_path, read_cache

import copy
import json
import posixpath
from pathlib import Path
from typing import Any


def implementation_requirement(
    requirement: dict[str, Any], project_root: Path, *, frontend: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Project visual references for model input without mutating requirement IR."""
    cache: dict[str, Any] = {}
    if frontend:
        path = cache_path(project_root / ".arc")
        if path.is_file():
            cache = read_cache(path)
    missing: list[str] = []

    def project(value: Any) -> Any:
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return copy.deepcopy(value)
        if "pages" in value and "visual_references" not in value:
            value = {**value, "visual_references": [page["reference"] for page in value["pages"] if page.get("reference")]}
        result = {}
        for key, item in value.items():
            if key != "visual_references":
                result[key] = project(item)
                continue
            if not frontend:
                continue
            analyses = []
            seen: set[str] = set()
            for reference in item:
                normalized = posixpath.normpath(reference.replace(chr(92), "/"))
                if normalized in seen:
                    continue
                seen.add(normalized)
                record = cache.get(normalized)
                if isinstance(record, dict) and isinstance(record.get("analysis"), dict):
                    analyses.append(copy.deepcopy(record["analysis"]))
                else:
                    missing.append(reference)
            result[key] = analyses
        return result

    return project(requirement), sorted(set(missing))


def frontend_subgraph(ir: dict[str, Any], requirement_id: str, target_ids: set[str]) -> dict[str, Any]:
    if ir.get("ir_kind") == "frontend_design":
        from compiler.frontend_design.context import value_references, required_types
        tables = ("ui", "data", "events", "functions")
        rows = {row["id"]: row for table in tables for row in (frontend_events(ir) if table == "events" else ir.get(table, []))}
        ui = {row["id"]: row for row in ir["ui"]}
        parents = {child: row["id"] for row in ui.values() for child in row["children"]}
        selected = (target_ids & rows.keys()) | {key for key, row in rows.items()
                                                if requirement_id in row.get("requirement_ids", [])}
        selected.update(set(ir.get('requirement_links', {}).get(requirement_id, {}).get('uses', [])) & rows.keys())
        # A component can contain UI from several requirements. Supply its whole
        # planned region so edits preserve the other surfaces and local handlers.
        from compiler.frontend_lowering.planning import plan_ui_units
        plan = plan_ui_units(ir)
        components = {plan["ui_units"][key] for key in selected if key in ui}
        components.update(plan["ui_units"][event["ui_id"]] for event in frontend_events(ir)
                          if event["id"] in selected)
        selected.update(key for unit in plan["units"] if unit["id"] in components for key in unit["ui_ids"])
        structural = set()
        for key in list(selected):
            seen = set()
            while key in parents and key not in seen:
                seen.add(key)
                key = parents[key]
                if key not in selected:
                    structural.add(key)
        selected.update(row["id"] for row in ir["data"] if row["scope_ui_id"] in selected)
        pending = list(selected)
        visited = set()
        while pending:
            key = pending.pop()
            if key in visited or key not in rows:
                continue
            visited.add(key)
            row = rows[key]
            refs = value_references(row, "data") | value_references(row, "item")
            refs.update(row.get("reads", []))
            refs.update(row.get("writes", []))
            refs.update(call["target_id"] for call in [*row.get("calls", []), *row.get("handlers", [])]
                        if call["kind"] == "FUNC")
            refs.update(event["id"] for event in frontend_events(ir) if event["ui_id"] == key)
            new = refs - selected
            selected.update(new)
            pending.extend(new)
        result = {table: [copy.deepcopy(row) for row in (frontend_events(ir) if table == "events" else ir[table]) if row["id"] in selected] for table in tables}
        result['structural_ui'] = [copy.deepcopy(row) for row in ir['ui'] if row['id'] in structural - selected]
        result['requirement_links'] = copy.deepcopy(ir.get('requirement_links', {}).get(requirement_id, {}))
        result.update(ir_kind="frontend_design", root_ui_id=ir["root_ui_id"], backend_ref=ir["backend_ref"],
                      entry_routes=sorted({row["route"] for row in result["ui"] if "route" in row}))
        result["types"] = required_types(result, ir["types"])
        return result
    raise ValueError("Expected the current frontend_design IR")


def visual_context(project_root: Path, requirement_id: str) -> list[dict[str, Any]]:
    root = project_root / ".arc/design/frontend"
    # All visual evidence comes from the one design-level analysis store.
    cached: dict[str, Any] = {}
    for key, row in read_cache(cache_path(project_root / ".arc")).items():
        identity = str(row.get("id") or row.get("reference_id") or key)
        cached[identity] = row

    # Traceability is the authoritative requirement -> visual link.  Older
    # caches may only use the image path as their key, so retain both ids and
    # paths while matching records.
    linked_ids: set[str] = set()
    linked_paths: set[str] = set()
    frontend: dict[str, Any] = {}
    frontend_path = root / "frontend_ir.json"
    if frontend_path.is_file():
        try:
            loaded = read_frontend_ir(frontend_path)
            frontend = loaded if isinstance(loaded, dict) else {}
        except (OSError, UnicodeError, ValueError):
            frontend = {}
    for table_rows in frontend.values():
        if not isinstance(table_rows, list):
            continue
        for row in table_rows:
            if not isinstance(row, dict) or requirement_id not in {
                str(value) for value in row.get("requirement_ids", [])
            }:
                continue
            linked_ids.update(str(value) for value in row.get("visual_reference_ids", []) if value)
            for key in ("visual_reference_path", "source_path"):
                if row.get(key):
                    linked_paths.add(str(row[key]).replace(chr(92), "/"))
    trace_path = root / "requirements.json"
    if not trace_path.is_file():
        trace_path = root / "traceability.json"
    if trace_path.is_file():
        try:
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            trace = {}
        links = trace.get("requirements", {}).get(requirement_id, {}) if isinstance(trace, dict) else {}
        if isinstance(links, dict):
            for key in ("visual_reference_ids", "visual_references"):
                values = links.get(key, [])
                if isinstance(values, list):
                    linked_ids.update(str(value) for value in values if value)
            for key in ("visual_reference_paths", "visual_paths"):
                values = links.get(key, [])
                if isinstance(values, list):
                    linked_paths.update(str(value).replace(chr(92), "/") for value in values if value)
    # Batch records retain the resolved reference id even when the final
    # traceability index intentionally omits image payloads.
    batch_root = root / "batches"
    if batch_root.is_dir():
        for batch_path in batch_root.glob("*.json"):
            try:
                batch = json.loads(batch_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(batch, dict) or requirement_id not in {
                str(value) for value in batch.get("requirement_ids", [])
            }:
                continue
            stack: list[Any] = [batch]
            while stack:
                value = stack.pop()
                if isinstance(value, dict):
                    for key, nested in value.items():
                        if key in {"visual_reference_id", "reference_id"} and nested:
                            linked_ids.add(str(nested))
                        elif key in {"visual_reference_path", "source_path"} and nested:
                            linked_paths.add(str(nested).replace(chr(92), "/"))
                        stack.append(nested)
                elif isinstance(value, list):
                    stack.extend(value)

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cache_key, raw in cached.items():
        if not isinstance(raw, dict):
            continue
        record = copy.deepcopy(raw)
        record_id = str(record.get("id", record.get("reference_id", cache_key)))
        source_path = str(record.get("source_path", record.get("path", cache_key))).replace(chr(92), "/")
        owns_requirement = requirement_id in {
            str(value) for value in record.get("requirement_ids", [])
        }
        linked = record_id in linked_ids or source_path in linked_paths
        # The canonical Frontend IR also records requirement/reference links.
        if frontend and not (owns_requirement or linked):
            for row in frontend.get("visual_references", []) if isinstance(frontend, dict) else []:
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("id", row.get("reference_id", "")))
                if requirement_id in {str(value) for value in row.get("requirement_ids", [])} and (
                    row_id == record_id or str(row.get("source_path", "")).replace(chr(92), "/") == source_path
                ):
                    linked = True
        if not (owns_requirement or linked):
            continue
        if record_id in seen:
            continue
        record.setdefault("id", record_id)
        records.append(record)
        seen.add(record_id)
    return records
