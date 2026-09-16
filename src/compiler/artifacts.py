from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

from arcbench_agent_runtime.jsonio import read_json, write_json_atomic


class CompilerArtifactStore:
    """Persist compact, stage-owned JSON symbol tables."""

    def __init__(self, output_dir: Path) -> None:
        self.root = output_dir.expanduser().resolve() / ".arc"
        self.frontend_root = self.root / "frontend"
        self.database_root = self.root / "database"
        self.design_root = self.root / "design"

    def write_frontend(
        self,
        *,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
    ) -> dict[str, str]:
        shutil.rmtree(self.frontend_root, ignore_errors=True)
        shutil.rmtree(self.design_root, ignore_errors=True)
        shutil.rmtree(self.root / "compiler", ignore_errors=True)
        shutil.rmtree(self.root / "cache" / "design", ignore_errors=True)
        paths = {
            "requirement_ir": self.frontend_root / "requirement_ir.json",
            "dependency_graph": self.frontend_root / "dependency_graph.json",
        }
        nodes = requirement_ir.get("nodes", {})
        requirements = [copy.deepcopy(nodes[key]) for key in requirement_ir.get("node_order", []) if key in nodes]
        if not requirements:
            requirements = [copy.deepcopy(value) for _, value in sorted(nodes.items()) if isinstance(value, dict)]
        atomic_dependencies = dependency_graph.get("atomic_dependencies", {})
        wave_by_requirement = {
            str(requirement_id): wave_index
            for wave_index, wave in enumerate(dependency_graph.get("implementation_waves", []), start=1)
            for requirement_id in wave
        }
        dependencies = []
        for requirement_id, values in sorted(dependency_graph.get("requirements", {}).items()):
            item = {
                "requirement_id": requirement_id,
                "dependencies": list(values) if isinstance(values, list) else [],
            }
            if requirement_id in atomic_dependencies:
                item["effective_atomic_dependencies"] = list(atomic_dependencies[requirement_id])
            if requirement_id in wave_by_requirement:
                item["implementation_wave"] = wave_by_requirement[requirement_id]
            dependencies.append(item)
        write_json_atomic(paths["requirement_ir"], requirements)
        write_json_atomic(paths["dependency_graph"], dependencies)
        return {name: str(path) for name, path in paths.items()}

    def write_queue(self, *, root_id: str | None, node_states: dict[str, str], frontend_ok: bool) -> str:
        return self._write_queue(
            frontend_status="COMPLETED" if frontend_ok else "FAILED",
            database_status="PENDING",
            design_status="PENDING",
            node_states=node_states,
        )

    def write_database(
        self,
        *,
        entities: dict[str, dict[str, Any]],
        relationships: list[dict[str, Any]],
    ) -> dict[str, str]:
        paths = {
            "database_schema": self.database_root / "database_schema.json",
            "database_relationships": self.database_root / "relationships.json",
        }
        write_json_atomic(paths["database_schema"], entities)
        write_json_atomic(paths["database_relationships"], relationships)
        return {name: str(path) for name, path in paths.items()}

    def read_database(self) -> tuple[dict[str, Any] | None, str | None]:
        """Read the persisted JSON ER graph into the internal structure."""

        schema_path = self.database_root / "database_schema.json"
        relationships_path = self.database_root / "relationships.json"
        missing = [path for path in (schema_path, relationships_path) if not path.is_file()]
        if missing:
            return None, f"Database artifact does not exist: {missing[0]}"
        entities = read_json(schema_path, None)
        relationships = read_json(relationships_path, None)
        if not isinstance(entities, dict) or any(not isinstance(value, dict) for value in entities.values()):
            return None, f"Database schema artifact must be keyed by entity id: {schema_path}"
        if not isinstance(relationships, list) or any(
            not isinstance(item, dict) or item.get("kind") != "RELATIONSHIP" for item in relationships
        ):
            return None, f"Relationships artifact must contain only RELATIONSHIP symbols: {relationships_path}"

        entity_rows = []
        constraints: list[dict[str, Any]] = []
        for entity_id, value in sorted(entities.items()):
            entity = copy.deepcopy(value)
            entity_constraints = entity.pop("constraints", [])
            if not isinstance(entity_constraints, list):
                return None, f"Entity constraints must be a list: {schema_path}#{entity_id}"
            for constraint in entity_constraints:
                if not isinstance(constraint, dict) or constraint.get("kind") != "CONSTRAINT":
                    return None, f"Entity constraints must contain only CONSTRAINT symbols: {schema_path}#{entity_id}"
                restored = copy.deepcopy(constraint)
                restored.pop("kind", None)
                constraints.append(restored)
            fields = entity.get("fields", [])
            if not isinstance(fields, list):
                return None, f"Entity fields must be a list: {schema_path}#{entity_id}"
            for field in fields:
                if not isinstance(field, dict):
                    return None, f"Entity fields must contain objects: {schema_path}#{entity_id}"
                field_constraints = field.pop("constraints", [])
                if not isinstance(field_constraints, list):
                    return None, f"Field constraints must be a list: {schema_path}#{entity_id}.{field.get('name', '')}"
                for constraint in field_constraints:
                    if not isinstance(constraint, dict) or constraint.get("kind") != "CONSTRAINT":
                        return None, f"Field constraints must contain only CONSTRAINT symbols: {schema_path}#{entity_id}.{field.get('name', '')}"
                    restored = copy.deepcopy(constraint)
                    restored.pop("kind", None)
                    restored["fields"] = [f"{entity_id}.{field.get('name', '')}"]
                    constraints.append(restored)
            entity_rows.append({**entity, "key": entity_id})

        def without_kind(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows = []
            for item in items:
                row = copy.deepcopy(item)
                row.pop("kind", None)
                rows.append(row)
            return rows

        return {
            "schema_version": 2,
            "status": "RESOLVED",
            "entities": entity_rows,
            "relationships": without_kind(relationships),
            "constraints": constraints,
        }, None

    def write_design(self, *, design_ir: dict[str, Any]) -> dict[str, str]:
        requirements = [
            copy.deepcopy(item.get("contract", {}))
            for item in design_ir.get("requirements", [])
            if isinstance(item, dict) and isinstance(item.get("contract"), dict)
        ]
        modules = [item for item in design_ir.get("modules", []) if isinstance(item, dict)]

        def compact_field(field: dict[str, Any]) -> dict[str, Any]:
            return {
                key: copy.deepcopy(field[key])
                for key in ("semantic_id", "name", "type", "required")
                if key in field
            }

        def compact_effect(effect: dict[str, Any]) -> dict[str, Any]:
            return {
                key: copy.deepcopy(effect[key])
                for key in ("id", "operation", "target", "fields", "action")
                if effect.get(key) is not None
            }

        def compact_module(module: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": str(module.get("id", "")),
                "spec": str(module.get("spec", "")),
                "inputs": [compact_field(item) for item in module.get("inputs", []) if isinstance(item, dict)],
                "outputs": [compact_field(item) for item in module.get("outputs", []) if isinstance(item, dict)],
                "effects": [compact_effect(item) for item in module.get("effects", []) if isinstance(item, dict)],
                "callers": sorted({str(value) for value in module.get("callers", []) if str(value)}),
                "callees": list(dict.fromkeys(str(value) for value in module.get("callees", []) if str(value))),
            }

        tables = {
            "design_requirement_contracts": (self.design_root / "requirement_contracts.json", requirements),
            "design_api_modules": (
                self.design_root / "api_modules.json",
                [compact_module(item) for item in modules if item.get("kind") == "API"],
            ),
            "design_function_modules": (
                self.design_root / "function_modules.json",
                [compact_module(item) for item in modules if item.get("kind") == "FUNC"],
            ),
            "design_db_modules": (
                self.design_root / "db_modules.json",
                [compact_module(item) for item in modules if item.get("kind") == "DB"],
            ),
        }
        for path, values in tables.values():
            write_json_atomic(path, values)
        return {name: str(path) for name, (path, _) in tables.items()}

    def write_pass_queue(
        self,
        *,
        root_id: str | None,
        node_states: dict[str, str],
        frontend_ok: bool,
        database_status: str,
        design_status: str = "PENDING",
    ) -> str:
        return self._write_queue(
            frontend_status="COMPLETED" if frontend_ok else "FAILED",
            database_status=database_status,
            design_status=design_status,
            node_states=node_states,
        )

    def _write_queue(
        self,
        *,
        frontend_status: str,
        database_status: str,
        design_status: str,
        node_states: dict[str, str],
    ) -> str:
        path = self.root / "processing_queue.json"
        statuses = (
            ("FRONTEND", frontend_status),
            ("DATABASE_SCHEMA", database_status),
            ("DESIGN", design_status),
            ("LOWERING", "PENDING"),
            ("IMPLEMENTATION", "PENDING"),
            ("ACCEPTANCE", "PENDING"),
        )
        state_rows = [
            {"requirement_id": requirement_id, "state": state}
            for requirement_id, state in sorted(node_states.items())
        ]
        rows = [
            {
                "pass_id": pass_id,
                "order": index,
                "status": status,
                "node_states": copy.deepcopy(state_rows) if pass_id in {"FRONTEND", "DATABASE_SCHEMA", "DESIGN"} else [],
            }
            for index, (pass_id, status) in enumerate(statuses)
        ]
        write_json_atomic(path, rows)
        return str(path)
