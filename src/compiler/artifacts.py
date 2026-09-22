from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from arcbench_agent_runtime.jsonio import write_json_atomic

from .design_projection import (
    project_api_contracts,
    project_api_modules,
    project_backend_module,
)
from .frontend_thin_design import validate_thin_frontend_design
from .frontend_thin_ir import (
    FRONTEND_DESIGN_TABLE_SCHEMAS,
    FRONTEND_IR_SCHEMA_VERSION,
    OPTIONAL_FRONTEND_DESIGN_TABLES,
    schema_shape_errors,
)


class CompilerArtifactStore:
    """Persist compact, stage-owned JSON symbol tables."""

    def __init__(self, output_dir: Path) -> None:
        self.root = output_dir.expanduser().resolve() / ".arc"
        # Preprocessing owns requirement normalization; product frontend design
        # artifacts may use .arc/frontend independently.
        self.preprocessing_root = self.root / "preprocessing"
        self.database_root = self.root / "database"
        self.fixtures_root = self.root / "fixtures"
        self.design_root = self.root / "design"
        self.backend_design_root = self.design_root / "backend"
        self.frontend_design_root = self.design_root / "frontend"
        self.backend_root = self.root / "backend"
        self.frontend_root = self.root / "frontend"
        self.code_root = self.root / "code"
        self.tests_root = self.root / "tests"

    def write_preprocessing(
        self,
        *,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
    ) -> dict[str, str]:
        shutil.rmtree(self.preprocessing_root, ignore_errors=True)
        shutil.rmtree(self.design_root, ignore_errors=True)
        paths = {
            "requirement_ir": self.preprocessing_root / "requirement_ir.json",
            "dependency_graph": self.preprocessing_root / "dependency_graph.json",
        }
        nodes = requirement_ir.get("nodes", {})
        requirements = [copy.deepcopy(nodes[key]) for key in requirement_ir.get("node_order", []) if key in nodes]
        if not requirements:
            requirements = [copy.deepcopy(value) for _, value in sorted(nodes.items()) if isinstance(value, dict)]
        atomic_dependencies = dependency_graph.get("atomic_dependencies", {})
        requirement_dependencies = dependency_graph.get("requirement_dependencies", {})
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
            if requirement_id in requirement_dependencies:
                item["effective_requirement_dependencies"] = list(
                    requirement_dependencies[requirement_id]
                )
            if requirement_id in wave_by_requirement:
                item["implementation_wave"] = wave_by_requirement[requirement_id]
            dependencies.append(item)
        write_json_atomic(paths["requirement_ir"], requirements)
        write_json_atomic(paths["dependency_graph"], dependencies)
        return {name: str(path) for name, path in paths.items()}

    def write_fixture_ir(self, fixture_ir: dict[str, Any]) -> str:
        path = self.fixtures_root / "fixture_ir.json"
        write_json_atomic(path, fixture_ir)
        return str(path)

    def read_fixture_ir(self) -> tuple[dict[str, Any] | None, str | None]:
        path = self.fixtures_root / "fixture_ir.json"
        if not path.is_file():
            return None, f"Fixture IR does not exist: {path}"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"Cannot read Fixture IR: {exc}"
        if not isinstance(payload, dict):
            return None, f"Fixture IR must contain an object: {path}"
        return payload, None

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

        tables = {
            "design_requirement_contracts": (self.design_root / "requirement_contracts.json", requirements),
            "design_api_contracts": (
                self.design_root / "api_contracts.json",
                project_api_contracts(design_ir),
            ),
            "design_api_modules": (
                self.backend_design_root / "api_modules.json",
                project_api_modules(design_ir),
            ),
            "design_function_modules": (
                self.backend_design_root / "function_modules.json",
                [project_backend_module(item) for item in modules if item.get("kind") == "FUNC"],
            ),
            "design_db_modules": (
                self.backend_design_root / "db_modules.json",
                [project_backend_module(item) for item in modules if item.get("kind") == "DB"],
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
        """Read and validate the shared contracts and Backend module tables."""

        table_paths = {
            "contracts": self.design_root / "requirement_contracts.json",
            "api_contracts": self.design_root / "api_contracts.json",
            "API": self.backend_design_root / "api_modules.json",
            "FUNC": self.backend_design_root / "function_modules.json",
            "DB": self.backend_design_root / "db_modules.json",
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

        api_contracts: dict[str, dict[str, Any]] = {}
        api_contract_keys = {"id", "spec", "inputs", "outputs", "effects"}
        for index, item in enumerate(tables["api_contracts"]):
            if not isinstance(item, dict):
                return None, (
                    "API contract must be an object: "
                    f"{table_paths['api_contracts']}[{index}]"
                )
            if set(item) != api_contract_keys:
                return None, (
                    "API contract must contain only shared interface fields "
                    f"{sorted(api_contract_keys)}: {table_paths['api_contracts']}[{index}]"
                )
            api_id = str(item.get("id", "")).strip()
            parts = api_id.split("::", 1)
            if len(parts) != 2 or not parts[0] or not parts[1].startswith("API."):
                return None, f"Invalid API contract id: {api_id!r}"
            if parts[0] not in contracts:
                return None, f"API contract {api_id} has no requirement contract"
            if api_id in api_contracts:
                return None, f"Duplicate API contract id: {api_id}"
            if not isinstance(item.get("spec"), str):
                return None, f"API contract {api_id} spec must be a string"
            for field_name in ("inputs", "outputs", "effects"):
                if not isinstance(item.get(field_name), list):
                    return None, f"API contract {api_id} field {field_name} must be a list"
            api_contracts[api_id] = copy.deepcopy(item)

        api_modules: list[dict[str, Any]] = []
        api_module_ids: set[str] = set()
        api_module_keys = {"id", "callees"}
        for index, item in enumerate(tables["API"]):
            if not isinstance(item, dict):
                return None, f"API module must be an object: {table_paths['API']}[{index}]"
            if set(item) != api_module_keys:
                return None, (
                    "API module must contain only Backend graph fields "
                    f"{sorted(api_module_keys)}: {table_paths['API']}[{index}]"
                )
            api_id = str(item.get("id", "")).strip()
            if api_id in api_module_ids:
                return None, f"Duplicate API module id: {api_id}"
            if api_id not in api_contracts:
                return None, f"API module {api_id} has no shared API contract"
            if not isinstance(item.get("callees"), list):
                return None, f"API module {api_id} callees must be a list"
            api_module_ids.add(api_id)
            api_modules.append({**copy.deepcopy(api_contracts[api_id]), **copy.deepcopy(item)})
        if api_module_ids != set(api_contracts):
            missing_modules = sorted(set(api_contracts) - api_module_ids)
            return None, f"Shared API contracts have no Backend API modules: {missing_modules}"

        modules: list[dict[str, Any]] = []
        module_by_id: dict[str, dict[str, Any]] = {}
        module_tables = {
            "API": api_modules,
            "FUNC": tables["FUNC"],
            "DB": tables["DB"],
        }
        for kind in ("API", "FUNC", "DB"):
            for index, item in enumerate(module_tables[kind]):
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
                for field_name in ("inputs", "outputs", "effects", "callees"):
                    if not isinstance(module.get(field_name), list):
                        return None, f"Design module {module_id} field {field_name} must be a list"
                module["callers"] = []
                module["kind"] = kind
                module["owner_requirement"] = owner_requirement
                module_by_id[module_id] = module
                modules.append(module)

        allowed_callee_kinds = {"API": {"FUNC"}, "FUNC": {"FUNC", "DB"}, "DB": set()}
        for module in modules:
            module_id = module["id"]
            references = [str(value).strip() for value in module["callees"]]
            if any(not value or value not in module_by_id for value in references):
                missing = sorted({value for value in references if value not in module_by_id})
                return None, f"Design module {module_id} has unknown callees: {missing}"
            if len(references) != len(set(references)):
                return None, f"Design module {module_id} has duplicate callees"
            module["callees"] = references
            for callee_id in module["callees"]:
                callee = module_by_id[str(callee_id)]
                if callee["kind"] not in allowed_callee_kinds[module["kind"]]:
                    return None, f"Invalid Design call edge: {module_id} -> {callee_id}"
                callee["callers"].append(module_id)
        for module in modules:
            module["callers"].sort()

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

    def write_frontend_design(
        self,
        *,
        frontend_ir: dict[str, Any],
    ) -> dict[str, str]:
        """Persist Frontend tables; requirement links remain in traceability."""

        issues = validate_thin_frontend_design(frontend_ir)
        if issues:
            raise ValueError(f"Cannot persist invalid Frontend Design IR: {issues[0].format()}")
        tables = {
            table_name: (
                self.frontend_design_root / f"{table_name}.json",
                copy.deepcopy(
                    frontend_ir.get(table_name, [])
                    if table_name in OPTIONAL_FRONTEND_DESIGN_TABLES
                    else frontend_ir[table_name]
                ),
            )
            for table_name in FRONTEND_DESIGN_TABLE_SCHEMAS
        }
        for path, values in tables.values():
            write_json_atomic(path, values)
        return {
            f"frontend_design_{table_name}": str(path)
            for table_name, (path, _) in tables.items()
        }

    def read_frontend_design(
        self,
        *,
        requirement_links: list[dict[str, Any]],
        expected_requirement_ids: set[str] | None = None,
        backend_api_ids: set[str] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Read Frontend tables and attach links owned by traceability."""

        table_paths = {
            table_name: self.frontend_design_root / f"{table_name}.json"
            for table_name in FRONTEND_DESIGN_TABLE_SCHEMAS
        }
        for table_name, path in table_paths.items():
            if not path.is_file() and table_name not in OPTIONAL_FRONTEND_DESIGN_TABLES:
                return None, f"Frontend Design artifact does not exist: {path}"

        tables: dict[str, list[Any]] = {}
        for table_name, path in table_paths.items():
            if not path.is_file():
                # An optional table predates this compiler version; the owning
                # pass regenerates it before the design is frozen again.
                tables[table_name] = []
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return None, f"Cannot read Frontend Design artifact {path}: {exc}"
            shape_errors = schema_shape_errors(
                payload,
                FRONTEND_DESIGN_TABLE_SCHEMAS[table_name],
                path=f"$.{table_name}",
            )
            if shape_errors:
                return None, f"Invalid Frontend Design artifact {path}: {shape_errors[0]}"
            tables[table_name] = payload

        frontend_ir = {
            "schema_version": FRONTEND_IR_SCHEMA_VERSION,
            **tables,
            "requirement_links": copy.deepcopy(requirement_links),
        }
        issues = validate_thin_frontend_design(
            frontend_ir,
            expected_requirement_ids=expected_requirement_ids,
            backend_api_ids=backend_api_ids,
        )
        if issues:
            return None, f"Frontend Design artifacts cannot be reused: {issues[0].format()}"
        return frontend_ir, None

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

    def write_frontend_symbol_registry(self, registry: dict[str, Any]) -> str:
        path = self.frontend_root / "symbol_registry.json"
        write_json_atomic(path, registry)
        return str(path)

    def write_frontend_file_registry(self, registry: dict[str, Any]) -> str:
        path = self.frontend_root / "file_registry.json"
        write_json_atomic(path, registry)
        return str(path)

    def write_frontend_lowering(
        self,
        *,
        route_registry: dict[str, Any],
        import_plan: dict[str, Any],
        manifest: dict[str, Any],
    ) -> dict[str, str]:
        paths = {
            "frontend_route_registry": self.frontend_root / "route_registry.json",
            "frontend_import_plan": self.frontend_root / "import_plan.json",
            "frontend_manifest": self.frontend_root / "manifest.json",
        }
        payloads = {
            "frontend_route_registry": route_registry,
            "frontend_import_plan": import_plan,
            "frontend_manifest": manifest,
        }
        for name, path in paths.items():
            write_json_atomic(path, payloads[name])
        return {name: str(path) for name, path in paths.items()}

    def write_code_bindings(self, registry: dict[str, Any]) -> str:
        path = self.code_root / "code_bindings.json"
        write_json_atomic(path, registry)
        return str(path)

    def read_code_bindings(self) -> tuple[dict[str, Any] | None, str | None]:
        path = self.code_root / "code_bindings.json"
        if not path.is_file():
            return None, f"Code Binding Registry does not exist: {path}"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"Cannot read Code Binding Registry: {exc}"
        if not isinstance(payload, dict):
            return None, f"Code Binding Registry must contain an object: {path}"
        return payload, None

    def write_test_environment_manifest(self, manifest: dict[str, Any]) -> str:
        path = self.tests_root / "environment_manifest.json"
        write_json_atomic(path, manifest)
        return str(path)

    def write_test_manifest(self, manifest: dict[str, Any]) -> str:
        path = self.tests_root / "test_manifest.json"
        write_json_atomic(path, manifest)
        return str(path)

    def read_test_manifest(self) -> tuple[dict[str, Any] | None, str | None]:
        path = self.tests_root / "test_manifest.json"
        if not path.is_file():
            return None, None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"Cannot read Test Manifest: {exc}"
        if not isinstance(payload, dict):
            return None, f"Test Manifest must contain an object: {path}"
        return payload, None

    def write_generated_tests(self, sources: dict[str, str]) -> dict[str, str]:
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
                or not normalized.startswith(
                    ("tests/unit/", "tests/integration/", "tests/e2e/")
                )
                or not normalized.endswith(".spec.ts")
            ):
                raise ValueError(f"Invalid generated test path: {relative!r}")
            target = (output_root / Path(normalized)).resolve()
            if output_root not in target.parents:
                raise ValueError(f"Generated test escapes output workspace: {relative!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(f"{target.suffix}.tmp")
            # Test Manifest hashes the canonical UTF-8 source bytes. Writing
            # text with newline=None translates LF to CRLF on Windows and
            # immediately invalidates the newly frozen SHA-256.
            temporary.write_bytes(content.encode("utf-8"))
            temporary.replace(target)
            artifacts[f"generated_test:{normalized}"] = str(target)
        return artifacts

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
                or not normalized.endswith((".ts", ".tsx"))
            ):
                raise ValueError(f"Invalid generated source path: {relative!r}")
            target = (output_root / Path(normalized)).resolve()
            if output_root != target and output_root not in target.parents:
                raise ValueError(f"Generated source escapes output workspace: {relative!r}")
            if not (
                normalized.startswith("backend/src/")
                or normalized.startswith("shared/src/")
                or normalized.startswith("frontend/src/")
                or normalized == "frontend/vite.config.ts"
            ):
                raise ValueError(f"Generated source is outside Stage 3 output roots: {relative!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(f"{target.suffix}.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
            artifacts[f"generated_source:{normalized}"] = str(target)
        return artifacts
