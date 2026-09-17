from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from arcbench_agent_runtime.jsonio import write_json_atomic


class CompilerArtifactStore:
    """Persist compact, stage-owned JSON symbol tables."""

    def __init__(self, output_dir: Path) -> None:
        self.root = output_dir.expanduser().resolve() / ".arc"
        self.frontend_root = self.root / "frontend"
        self.database_root = self.root / "database"
        self.design_root = self.root / "design"
        self.backend_root = self.root / "backend"

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
        try:
            entities = json.loads(schema_path.read_text(encoding="utf-8"))
            relationships = json.loads(relationships_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"Cannot read Database artifacts: {exc}"
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

    def read_design(
        self,
        *,
        expected_requirement_ids: set[str] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Read and validate the four persisted Design symbol tables."""

        table_paths = {
            "contracts": self.design_root / "requirement_contracts.json",
            "API": self.design_root / "api_modules.json",
            "FUNC": self.design_root / "function_modules.json",
            "DB": self.design_root / "db_modules.json",
        }
        for path in table_paths.values():
            if not path.is_file():
                return None, f"Design artifact does not exist: {path}"

        tables: dict[str, list[Any]] = {}
        for name, path in table_paths.items():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return None, f"Cannot read Design artifact {path}: {exc}"
            if not isinstance(payload, list):
                return None, f"Design artifact must be a JSON list: {path}"
            tables[name] = payload

        contracts: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(tables["contracts"]):
            if not isinstance(item, dict):
                return None, f"Design contract must be an object: {table_paths['contracts']}[{index}]"
            requirement_id = str(item.get("requirement_id", "")).strip()
            if not requirement_id:
                return None, f"Design contract has no requirement_id: {table_paths['contracts']}[{index}]"
            if requirement_id in contracts:
                return None, f"Duplicate Design contract for requirement: {requirement_id}"
            contracts[requirement_id] = copy.deepcopy(item)
        if expected_requirement_ids is not None and set(contracts) != expected_requirement_ids:
            missing = sorted(expected_requirement_ids - set(contracts))
            extra = sorted(set(contracts) - expected_requirement_ids)
            return None, f"Design requirements do not match current requirements; missing={missing}, extra={extra}"

        modules: list[dict[str, Any]] = []
        module_by_id: dict[str, dict[str, Any]] = {}
        for kind in ("API", "FUNC", "DB"):
            for index, item in enumerate(tables[kind]):
                if not isinstance(item, dict):
                    return None, f"{kind} module must be an object: {table_paths[kind]}[{index}]"
                module_id = str(item.get("id", "")).strip()
                parts = module_id.split("::", 1)
                expected_prefix = f"{kind}."
                if len(parts) != 2 or not parts[0] or not parts[1].startswith(expected_prefix):
                    return None, f"Invalid {kind} module id: {module_id!r}"
                if module_id in module_by_id:
                    return None, f"Duplicate Design module id: {module_id}"
                owner_requirement = parts[0]
                if owner_requirement not in contracts:
                    return None, f"Design module {module_id} has no requirement contract"
                module = copy.deepcopy(item)
                for field_name in ("inputs", "outputs", "effects", "callers", "callees"):
                    if not isinstance(module.get(field_name), list):
                        return None, f"Design module {module_id} field {field_name} must be a list"
                module["kind"] = kind
                module["owner_requirement"] = owner_requirement
                module_by_id[module_id] = module
                modules.append(module)

        allowed_callee_kinds = {"API": {"FUNC"}, "FUNC": {"FUNC", "DB"}, "DB": set()}
        for module in modules:
            module_id = module["id"]
            for direction in ("callers", "callees"):
                references = [str(value).strip() for value in module[direction]]
                if any(not value or value not in module_by_id for value in references):
                    missing = sorted({value for value in references if value not in module_by_id})
                    return None, f"Design module {module_id} has unknown {direction}: {missing}"
                if len(references) != len(set(references)):
                    return None, f"Design module {module_id} has duplicate {direction}"
                module[direction] = references
            for callee_id in module["callees"]:
                callee = module_by_id[str(callee_id)]
                if callee["kind"] not in allowed_callee_kinds[module["kind"]]:
                    return None, f"Invalid Design call edge: {module_id} -> {callee_id}"
                if module_id not in callee["callers"]:
                    return None, f"Design call edge is not reciprocal: {module_id} -> {callee_id}"
            for caller_id in module["callers"]:
                if module_id not in module_by_id[str(caller_id)]["callees"]:
                    return None, f"Design caller edge is not reciprocal: {caller_id} -> {module_id}"

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module_id: str) -> bool:
            if module_id in visiting:
                return False
            if module_id in visited:
                return True
            visiting.add(module_id)
            if any(not visit(str(callee)) for callee in module_by_id[module_id]["callees"]):
                return False
            visiting.remove(module_id)
            visited.add(module_id)
            return True

        if any(not visit(module_id) for module_id in sorted(module_by_id)):
            return None, "Design module invocation graph contains a cycle"

        requirements = [
            {
                "id": requirement_id,
                "contract": contract,
                "api_ids": sorted(
                    module["id"]
                    for module in modules
                    if module["kind"] == "API" and module["owner_requirement"] == requirement_id
                ),
            }
            for requirement_id, contract in sorted(contracts.items())
        ]
        return {"requirements": requirements, "modules": modules}, None

    def read_project_manifest(self) -> tuple[dict[str, Any] | None, str | None]:
        """Validate the project-initialization boundary before Skeleton starts."""

        path = self.root / "project" / "project-manifest.json"
        if not path.is_file():
            return None, f"Project manifest does not exist: {path}"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"Cannot read project manifest {path}: {exc}"
        if not isinstance(manifest, dict) or manifest.get("status") != "PROJECT_INITIALIZED":
            return None, f"Project manifest is not initialized: {path}"
        allowed = manifest.get("allowedOutputRoots", {}).get("skeleton", [])
        required_roots = {"backend/src", "shared/src/contracts", "shared/src/index.ts"}
        if not isinstance(allowed, list) or not required_roots <= {str(value) for value in allowed}:
            return None, f"Project manifest has incomplete Skeleton output roots: {path}"
        workspaces = manifest.get("workspaces")
        if not isinstance(workspaces, dict):
            return None, f"Project manifest has no workspace map: {path}"
        output_root = self.root.parent
        for name in ("frontend", "backend", "shared"):
            item = workspaces.get(name)
            relative_root = str(item.get("root", "")).strip() if isinstance(item, dict) else ""
            if not relative_root or not (output_root / relative_root).is_dir():
                return None, f"Initialized project workspace is missing: {name}"
        shared_link = output_root / "node_modules" / "@arc" / "shared"
        expected_shared = (output_root / "shared").resolve()
        actual_shared = shared_link.resolve()
        if not shared_link.exists() or actual_shared != expected_shared:
            return None, (
                "Initialized @arc/shared workspace link is invalid: "
                f"expected {expected_shared}, resolved {actual_shared}"
            )
        return manifest, None

    def write_symbol_registry(self, registry: dict[str, Any]) -> str:
        path = self.backend_root / "symbol_registry.json"
        write_json_atomic(path, registry)
        return str(path)

    def write_file_registry(self, registry: dict[str, Any]) -> str:
        path = self.backend_root / "file_registry.json"
        write_json_atomic(path, registry)
        return str(path)

    def write_type_manifest(self, manifest: dict[str, Any]) -> str:
        path = self.backend_root / "type_manifest.json"
        write_json_atomic(path, manifest)
        return str(path)

    def write_database_schema_manifest(self, manifest: dict[str, Any]) -> str:
        path = self.backend_root / "database_schema_manifest.json"
        write_json_atomic(path, manifest)
        return str(path)

    def write_module_manifest(
        self,
        module_kind: str,
        manifest: dict[str, Any],
    ) -> str:
        filenames = {
            "DB": "db_modules_manifest.json",
            "FUNC": "func_modules_manifest.json",
            "API": "api_modules_manifest.json",
        }
        kind = str(module_kind).upper()
        if kind not in filenames:
            raise ValueError(f"Unsupported module manifest kind: {module_kind!r}")
        path = self.backend_root / filenames[kind]
        write_json_atomic(path, manifest)
        return str(path)

    def write_backend_lowering(
        self,
        *,
        route_registry: dict[str, Any],
        import_plan: dict[str, Any],
        manifest: dict[str, Any],
    ) -> dict[str, str]:
        paths = {
            "backend_route_registry": self.backend_root / "route_registry.json",
            "backend_import_plan": self.backend_root / "import_plan.json",
            "backend_manifest": self.backend_root / "manifest.json",
        }
        payloads = {
            "backend_route_registry": route_registry,
            "backend_import_plan": import_plan,
            "backend_manifest": manifest,
        }
        for name, path in paths.items():
            write_json_atomic(path, payloads[name])
        return {name: str(path) for name, path in paths.items()}

    def write_generated_sources(self, sources: dict[str, str]) -> dict[str, str]:
        """Atomically materialize compiler-planned source files inside the output workspace."""

        output_root = self.root.parent
        artifacts: dict[str, str] = {}
        for relative, content in sorted(sources.items()):
            normalized = str(relative).replace("\\", "/").strip().strip("/")
            path = PurePosixPath(normalized)
            if (
                not normalized
                or path.is_absolute()
                or "." in path.parts
                or ".." in path.parts
                or not normalized.endswith(".ts")
            ):
                raise ValueError(f"Invalid generated source path: {relative!r}")
            target = (output_root / Path(normalized)).resolve()
            if output_root != target and output_root not in target.parents:
                raise ValueError(f"Generated source escapes output workspace: {relative!r}")
            if not (
                normalized.startswith("backend/src/")
                or normalized.startswith("shared/src/")
            ):
                raise ValueError(f"Generated source is outside Stage 3 output roots: {relative!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(f"{target.suffix}.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
            artifacts[f"generated_source:{normalized}"] = str(target)
        return artifacts

    def write_pass_queue(
        self,
        *,
        root_id: str | None,
        node_states: dict[str, str],
        frontend_ok: bool,
        database_status: str,
        design_status: str = "PENDING",
        project_status: str = "PENDING",
        lowering_status: str = "PENDING",
    ) -> str:
        return self._write_queue(
            frontend_status="COMPLETED" if frontend_ok else "FAILED",
            database_status=database_status,
            design_status=design_status,
            project_status=project_status,
            lowering_status=lowering_status,
            node_states=node_states,
        )

    def _write_queue(
        self,
        *,
        frontend_status: str,
        database_status: str,
        design_status: str,
        project_status: str = "PENDING",
        lowering_status: str = "PENDING",
        node_states: dict[str, str],
    ) -> str:
        path = self.root / "processing_queue.json"
        statuses = (
            ("FRONTEND", frontend_status),
            ("DATABASE_SCHEMA", database_status),
            ("DESIGN", design_status),
            ("PROJECT_INITIALIZATION", project_status),
            ("LOWERING", lowering_status),
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
                "node_states": copy.deepcopy(state_rows)
                if pass_id in {"FRONTEND", "DATABASE_SCHEMA", "DESIGN"}
                else [],
            }
            for index, (pass_id, status) in enumerate(statuses)
        ]
        write_json_atomic(path, rows)
        return str(path)
