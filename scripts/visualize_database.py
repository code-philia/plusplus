from __future__ import annotations

"""Render Stage 1 database artifacts as a Mermaid ER diagram in Markdown."""

import argparse
import json
import re
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_database_dir(value: Path) -> Path:
    root = value.expanduser().resolve()
    for candidate in (root, root / "database", root / ".arc" / "database"):
        if all((candidate / name).is_file() for name in ("database_schema.json", "relationships.json")):
            return candidate
    raise SystemExit(f"Cannot find Stage 1 database artifacts below {root}")


def mermaid_id(value: Any) -> str:
    result = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "unknown")).strip("_")
    if not result:
        result = "unknown"
    if result[0].isdigit():
        result = f"entity_{result}"
    return result.upper()


def mermaid_type(value: Any) -> str:
    result = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "unknown")).strip("_")
    return result or "unknown"


def clean_comment(value: Any, limit: int = 110) -> str:
    text = " ".join(str(value or "").replace('"', "'").split())
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def constraint_types(field: dict[str, Any]) -> set[str]:
    values = {
        str(item.get("type") or "").upper()
        for item in field.get("constraints", [])
        if isinstance(item, dict)
    }
    if field.get("primary_key"):
        values.add("PRIMARY_KEY")
    if field.get("unique"):
        values.add("UNIQUE")
    if field.get("references") or field.get("foreign_key"):
        values.add("FOREIGN_KEY")
    return values


def field_line(field: dict[str, Any]) -> str:
    types = constraint_types(field)
    keys = []
    if "PRIMARY_KEY" in types:
        keys.append("PK")
    if "FOREIGN_KEY" in types:
        keys.append("FK")
    if "UNIQUE" in types:
        keys.append("UK")

    comments = []
    description = clean_comment(field.get("description"))
    if description:
        comments.append(description)
    nullable = field.get("nullable")
    if nullable is False and "PRIMARY_KEY" not in types:
        comments.append("required")
    check_descriptions = [
        clean_comment(item.get("description"), 70)
        for item in field.get("constraints", [])
        if isinstance(item, dict) and str(item.get("type") or "").upper() == "CHECK"
    ]
    comments.extend(value for value in check_descriptions if value)

    parts = [
        mermaid_type(field.get("type")),
        mermaid_id(field.get("name")).lower(),
    ]
    if keys:
        parts.append(", ".join(keys))
    if comments:
        comment = clean_comment("; ".join(comments))
        parts.append(f'"{comment}"')
    return "        " + " ".join(parts)


def relationship_line(item: dict[str, Any]) -> str | None:
    parent = mermaid_id(item.get("parent"))
    child = mermaid_id(item.get("child"))
    if parent == "UNKNOWN" or child == "UNKNOWN":
        return None

    relation_type = str(item.get("type") or "ONE_TO_MANY").upper()
    child_required = bool(item.get("child_required", False))
    if relation_type == "ONE_TO_ONE":
        cardinality = "||--||" if child_required else "||--o|"
    elif relation_type == "MANY_TO_MANY":
        cardinality = "}o--o{"
    else:
        cardinality = "||--|{" if child_required else "||--o{"

    label = clean_comment(
        item.get("description") or item.get("fk_field") or relation_type.lower(),
        72,
    )
    return f'    {parent} {cardinality} {child} : "{label}"'


def render(
    entities: dict[str, dict[str, Any]],
    relationships: list[dict[str, Any]],
    source: Path,
) -> str:
    lines = [
        "# Database ER Diagram",
        "",
        f"Source: `{source}`",
        "",
        "```mermaid",
        "erDiagram",
    ]
    for entity_id, entity in entities.items():
        lines.append(f"    {mermaid_id(entity_id)} {{")
        fields = entity.get("fields", []) if isinstance(entity, dict) else []
        for field in fields:
            if isinstance(field, dict):
                lines.append(field_line(field))
        lines.append("    }")
        lines.append("")

    relation_lines = [relationship_line(item) for item in relationships]
    lines.extend(value for value in relation_lines if value)
    lines.extend(["```", ""])

    entity_constraints = []
    for entity_id, entity in entities.items():
        for constraint in entity.get("constraints", []) if isinstance(entity, dict) else []:
            if not isinstance(constraint, dict):
                continue
            description = clean_comment(constraint.get("description"), 180)
            constraint_type = str(constraint.get("type") or "CONSTRAINT")
            entity_constraints.append(f"- `{entity_id}` · **{constraint_type}**: {description or '-'}")
    if entity_constraints:
        lines.extend(["## Entity constraints", "", *entity_constraints, ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Stage 1 database artifacts as a Mermaid ER diagram.")
    parser.add_argument("input", type=Path, help="Workspace, .arc/database, or database artifact directory")
    parser.add_argument("-o", "--output", type=Path, help="Output Markdown path")
    args = parser.parse_args()

    database_dir = resolve_database_dir(args.input)
    entities = read_json(database_dir / "database_schema.json")
    relationships = read_json(database_dir / "relationships.json")
    if not isinstance(entities, dict) or any(not isinstance(item, dict) for item in entities.values()):
        raise SystemExit("database_schema.json must be an object keyed by entity id")
    if not isinstance(relationships, list) or any(not isinstance(item, dict) for item in relationships):
        raise SystemExit("relationships.json must be a JSON list of objects")

    workspace = database_dir.parent.parent if database_dir.parent.name == ".arc" else database_dir.parent
    output = (args.output or workspace / "visualizations" / "database-er.md").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(entities, relationships, database_dir), encoding="utf-8")
    print(f"Wrote {output} ({len(entities)} entities, {len(relationships)} relationships)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
