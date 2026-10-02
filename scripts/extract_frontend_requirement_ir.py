from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(r"D:\project\agentic-requirement-compiler\workspace\smoke-1\.arc\design\frontend")
TARGET = "REQ-1-1"
NORMALIZED_TARGET = TARGET.replace("-", ".")
ENTITIES = ("components", "data", "ui", "properties", "events", "handlers", "effects")
OUT_JSON = ROOT / "req-1-1-frontend-ir.json"
OUT_MMD = ROOT / "req-1-1-frontend-ir.mmd"


def requirement_matches(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return any(str(item).replace("-", ".") == NORMALIZED_TARGET for item in value)


def referenced_ids(value: Any, known: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str) and value in known:
        found.add(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            # IDs mentioned in prose, names, and requirement labels are not
            # graph references. Only inspect structural fields and expression
            # payloads to avoid pulling the global application root into a
            # requirement-local extraction.
            if key in {"name", "spec", "requirement_ids", "description", "text"}:
                continue
            found.update(referenced_ids(item, known))
    elif isinstance(value, list):
        for item in value:
            found.update(referenced_ids(item, known))
    return found


def short_label(row: dict[str, Any]) -> str:
    ident = str(row.get("id", ""))
    name = re.sub(r"[\n\r]+", " ", str(row.get("name", ""))).strip()
    return f"{ident} {name}".strip()


def main() -> None:
    records: dict[str, list[dict[str, Any]]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    entity_by_id: dict[str, str] = {}
    for entity in ENTITIES:
        payload = json.loads((ROOT / f"{entity}.json").read_text(encoding="utf-8"))
        rows = [row for row in payload.get(entity, []) if isinstance(row, dict) and row.get("id")]
        records[entity] = rows
        for row in rows:
            ident = str(row["id"])
            by_id[ident] = row
            entity_by_id[ident] = entity

    metadata = json.loads((ROOT / "metadata.json").read_text(encoding="utf-8"))
    root_component_id = str(metadata.get("root_component_id", ""))
    root_ui_ids = {
        str(row.get("ui_root_id"))
        for row in records["components"]
        if str(row.get("id", "")) == root_component_id and row.get("ui_root_id")
    }

    selected: dict[str, set[str]] = {entity: set() for entity in ENTITIES}
    for entity, rows in records.items():
        for row in rows:
            if requirement_matches(row.get("requirement_ids")):
                ident = str(row["id"])
                if ident not in root_ui_ids and ident != root_component_id:
                    selected[entity].add(ident)

    # Expand only the local IR reference closure: UI trees, owning components,
    # data/property references, and event-handler-effect wiring.
    changed = True
    while changed:
        changed = False
        all_selected = set().union(*(selected[entity] for entity in ENTITIES))
        for entity in ENTITIES:
            for ident in list(selected[entity]):
                row = by_id[ident]
                if ident in root_ui_ids or ident == root_component_id:
                    continue
                refs = referenced_ids(row, set(by_id))
                for ref in refs:
                    ref_entity = entity_by_id[ref]
                    if ref not in selected[ref_entity]:
                        selected[ref_entity].add(ref)
                        changed = True
        # ui_data is a derived association table, handled below; this pass
        # deliberately does not expand through arbitrary prose/spec strings.

    output: dict[str, Any] = {
        "requirement_id": TARGET,
        "normalized_requirement_id": NORMALIZED_TARGET,
        "source_directory": str(ROOT),
        "entities": {},
        "ui_data": {},
    }
    for entity in ENTITIES:
        output["entities"][entity] = [
            by_id[ident] for ident in sorted(selected[entity])
        ]
    ui_data_path = ROOT / "ui_data.json"
    if ui_data_path.exists():
        ui_data = json.loads(ui_data_path.read_text(encoding="utf-8")).get("ui_data", {})
        output["ui_data"] = {
            ident: ui_data[ident]
            for ident in sorted(selected["ui"])
            if ident in ui_data
        }
    output["counts"] = {entity: len(output["entities"][entity]) for entity in ENTITIES}
    OUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "%% REQ-1.1 frontend IR (extracted from seven entities)",
        "flowchart LR",
    ]
    for entity in ENTITIES:
        lines.append(f"  subgraph {entity.upper()}[\"{entity}\"]")
        for row in output["entities"][entity]:
            ident = str(row["id"])
            node_id = ident.replace(".", "_")
            label = short_label(row).replace('"', "'")
            lines.append(f'    {node_id}["{label}"]')
        lines.append("  end")

    edge_seen: set[tuple[str, str, str]] = set()
    selected_ids = set().union(*(selected[entity] for entity in ENTITIES))
    for source_id in sorted(selected_ids):
        source = by_id[source_id]
        source_node = source_id.replace(".", "_")
        for target_id in sorted(referenced_ids(source, selected_ids)):
            if target_id == source_id:
                continue
            target_node = target_id.replace(".", "_")
            relation = "references"
            if source.get("children") and target_id in source.get("children", []):
                relation = "contains"
            elif source.get("component_id") == target_id:
                relation = "owned_by"
            elif source.get("handler_id") == target_id:
                relation = "handled_by"
            elif target_id.startswith("F."):
                relation = "invokes"
            elif target_id.startswith("P.") or target_id.startswith("D."):
                relation = "uses"
            edge = (source_node, target_node, relation)
            if edge not in edge_seen:
                edge_seen.add(edge)
                lines.append(f'  {source_node} -->|{relation}| {target_node}')
    OUT_MMD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "mermaid": str(OUT_MMD), "counts": output["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
