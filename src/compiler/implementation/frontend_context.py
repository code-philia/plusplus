"""Read-only context selection for the canonical frontend IR."""
from __future__ import annotations

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
        path = project_root / ".arc/design/frontend/visual_cache.json"
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cache = {posixpath.normpath(key.replace(chr(92), "/")): row
                         for key, row in loaded.items()}
    missing: list[str] = []

    def project(value: Any) -> Any:
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return copy.deepcopy(value)
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
        rows = {row["id"]: row for table in tables for row in ir.get(table, [])}
        ui = {row["id"]: row for row in ir["ui"]}
        parents = {child: row["id"] for row in ui.values() for child in row["children"]}
        selected = (target_ids & rows.keys()) | {key for key, row in rows.items()
                                                if requirement_id in row.get("requirement_ids", [])}
        selected.update(set(ir.get('requirement_links', {}).get(requirement_id, {}).get('uses', [])) & rows.keys())
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
            refs.update(event["id"] for event in ir["events"] if event["ui_id"] == key)
            new = refs - selected
            selected.update(new)
            pending.extend(new)
        result = {table: [copy.deepcopy(row) for row in ir[table] if row["id"] in selected] for table in tables}
        result['structural_ui'] = [copy.deepcopy(row) for row in ir['ui'] if row['id'] in structural - selected]
        result['requirement_links'] = copy.deepcopy(ir.get('requirement_links', {}).get(requirement_id, {}))
        result.update(ir_kind="frontend_design", root_ui_id=ir["root_ui_id"], backend_ref=ir["backend_ref"],
                      entry_routes=sorted({row["route"] for row in result["ui"] if "route" in row}))
        result["types"] = required_types(result, ir["types"])
        return result
    raise ValueError("Expected the current frontend_design IR")


def visual_context(project_root: Path, requirement_id: str) -> list[dict[str, Any]]:
    root = project_root / ".arc/design/frontend"
    # The exported reference table may be empty while the path-keyed cache
    # contains the actual analyses. Read both; an index must not mask evidence.
    cached: dict[str, Any] = {}
    for filename in ("visual_cache.json", "visual-references.json", "visual_references.json"):
        path = root / filename
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        rows = payload.get("visual_references")
        entries = enumerate(rows) if isinstance(rows, list) else payload.items()
        for key, row in entries:
            if not isinstance(row, dict):
                continue
            identity = str(row.get("id") or row.get("reference_id") or key)
            previous = cached.get(identity, {})
            merged = {**previous, **row}
            if previous.get("analysis") and not row.get("analysis"):
                merged["analysis"] = previous["analysis"]
            merged["requirement_ids"] = sorted(set(previous.get("requirement_ids", []))
                                               | set(row.get("requirement_ids", [])))
            merged.setdefault("source_path", str(key))
            cached[identity] = merged

    # Traceability is the authoritative requirement -> visual link.  Older
    # caches may only use the image path as their key, so retain both ids and
    # paths while matching records.
    linked_ids: set[str] = set()
    linked_paths: set[str] = set()
    frontend: dict[str, Any] = {}
    frontend_path = root / "frontend_ir.json"
    if frontend_path.is_file():
        try:
            loaded = json.loads(frontend_path.read_text(encoding="utf-8"))
            frontend = loaded if isinstance(loaded, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
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
