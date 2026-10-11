from __future__ import annotations

import json
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from utils.jsonio import write_json_atomic
from compiler.ir.frontend_storage import write_frontend_ir


class CompilerArtifactStore:
    """Persist compact, stage-owned JSON symbol tables."""

    def __init__(self, output_dir: Path) -> None:
        self.root = output_dir.expanduser().resolve() / ".arc"
        self.preprocessing_root = self.root / "preprocessing"
        self.design_root = self.root / "design"
        self.database_root = self.design_root / "database"
        self.backend_design_root = self.design_root / "backend"
        self.frontend_design_root = self.design_root / "frontend"
        self.lowering_root = self.root / "lowering"
        self.backend_lowering_root = self.lowering_root / "backend"
        self.frontend_lowering_root = self.lowering_root / "frontend"
        self.tests_root = self.root / "tests"

    def write_preprocessing(
        self,
        *,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
    ) -> dict[str, str]:
        paths = {
            "requirement_ir": self.preprocessing_root / "requirement_ir.json",
            "dependency_graph": self.preprocessing_root / "dependency_graph.json",
        }
        write_json_atomic(paths["requirement_ir"], requirement_ir)
        write_json_atomic(paths["dependency_graph"], dependency_graph)
        return {name: str(path) for name, path in paths.items()}

    def write_database_schema(self, schema: dict[str, Any], *, er_model: dict[str, Any] | None = None) -> str:
        path = self.database_root / "schema.json"
        write_json_atomic(path, schema)
        if er_model is not None:
            write_json_atomic(self.database_root / "er_model.json", er_model)
        return str(path)


    def write_design(self, *, design_ir: dict[str, Any]) -> dict[str, str]:
        if design_ir.get("ir_kind") != "backend_design":
            raise ValueError("Expected the current canonical Backend IR")
        modules = design_ir.get("modules", [])
        paths = {
            "design_modules": self.backend_design_root / "modules.json",
            "design_modules_db": self.backend_design_root / "modules_db.json",
            "design_modules_func": self.backend_design_root / "modules_func.json",
            "design_modules_api": self.backend_design_root / "modules_api.json",
        }
        paths["backend_ir"] = self.backend_design_root / "backend_ir.json"
        write_json_atomic(paths["backend_ir"], design_ir)
        paths["backend_design_report"] = self.backend_design_root / "report.json"
        write_json_atomic(paths["design_modules"], {"modules": modules})
        for kind, key in (("DB", "design_modules_db"), ("FUNC", "design_modules_func"), ("API", "design_modules_api")):
            write_json_atomic(paths[key], {"modules": [row for row in modules if row.get("kind") == kind]})
        return {name: str(path) for name, path in paths.items()}

    def write_frontend_design(
        self, *, frontend_ir: dict[str, Any], report: dict[str, Any],
        batches: list[dict[str, Any]],
        traceability: dict[str, Any],
    ) -> dict[str, str]:
        """Persist the canonical frontend design and its derived tables."""
        from compiler.frontend_design import ui_data_associations
        paths: dict[str, str] = {}
        canonical_path = self.frontend_design_root / "frontend_ir.json"
        write_frontend_ir(canonical_path, frontend_ir)
        paths["frontend_ir"] = str(canonical_path)
        associations_path = self.frontend_design_root / 'requirement_links.json'
        write_json_atomic(associations_path, {'requirement_links': frontend_ir.get('requirement_links', {})})
        paths['frontend_requirement_links'] = str(associations_path)
        paths["frontend_ui"] = str(self.frontend_design_root / "ui.json")
        tables = ("data", "functions", "types", "pending_capabilities")
        for table in tables:
            path = self.frontend_design_root / f"{table}.json"
            write_json_atomic(path, {table: frontend_ir.get(table, [])})
            paths[f"frontend_{table}"] = str(path)
        metadata_path = self.frontend_design_root / "metadata.json"
        write_json_atomic(metadata_path, {key: frontend_ir[key] for key in
            ("ir_kind", "backend_ref", "root_ui_id") if key in frontend_ir})
        paths["frontend_metadata"] = str(metadata_path)
        visual_path = self.frontend_design_root / "visual_references.json"
        write_json_atomic(visual_path, {"analysis_ref": "../visual_cache.json",
                                      "visual_references": frontend_ir.get("visual_references", [])})
        report_path = self.frontend_design_root / "report.json"
        write_json_atomic(report_path, report)
        requirements_path = self.frontend_design_root / "requirements.json"
        ui_data_path = self.frontend_design_root / "ui_data.json"
        write_json_atomic(requirements_path, {"requirements": traceability})
        write_json_atomic(ui_data_path, {"ui_data": ui_data_associations(frontend_ir)})
        paths.update({"frontend_visual_references": str(visual_path),
                      "visual_cache": str(self.design_root / "visual_cache.json"),
                      "frontend_report": str(report_path),
                      "frontend_requirements": str(requirements_path),
                      "frontend_ui_data": str(ui_data_path)})
        return paths

    def write_frontend_lowering(
        self, *, report: dict[str, Any], sources: dict[str, str], batches: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Keep failed attempts reviewable without replacing a working frontend."""
        root = self.frontend_lowering_root
        if not hasattr(self, "_previous_frontend_sources"):
            installed = root / "installed-sources.json"
            previous = installed if installed.is_file() else root / "sources.json"
            self._previous_frontend_sources = json.loads(previous.read_text(encoding="utf-8")) if previous.is_file() else {}
            # Keep published-file ownership separate from a failed candidate generation.
            if not installed.is_file():
                write_json_atomic(installed, self._previous_frontend_sources)
        report_path = root / "lowering.json"
        write_json_atomic(report_path, report)
        write_json_atomic(root / "bindings.json", {"bindings": report.get("bindings", [])})
        write_json_atomic(root / "ui_plan.json", report.get("ui_plan", {}))
        write_json_atomic(root / "module_plan.json", report.get("ui_plan", {}))
        write_json_atomic(root / "function_plan.json", report.get("ui_plan", {}).get("function_plan", []))
        write_json_atomic(root / "implementation_tasks.json", {
            "implementation_tasks": report.get("implementation_tasks", [])
        })
        # Candidate source remains an artifact until every local implementation succeeds.
        candidate_path = root / "sources.json"
        write_json_atomic(candidate_path, sources)
        return {"frontend_lowering_report": str(report_path), "frontend_lowering_sources": str(candidate_path)}

    def write_backend_lowering(
        self, *, report: dict[str, Any], sources: dict[str, str],
        tables: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Persist backend lowering in the same report/source shape as frontend."""
        root = self.backend_lowering_root
        report_path = root / "lowering.json"
        sources_path = root / "sources.json"
        write_json_atomic(report_path, report)
        write_json_atomic(sources_path, sources)
        for name, value in (tables or {}).items():
            write_json_atomic(root / f"{name}.json", value)
        return {"backend_lowering_report": str(report_path),
                "backend_lowering_sources": str(sources_path)}

    def write_frontend_sources(self, sources: dict[str, str]) -> dict[str, str]:
        """Retire unchanged compiler-owned files after component renaming, preserving recoverability."""
        workspace = self.root.parent.resolve()
        frontend_root = (workspace / "frontend" / "src").resolve()
        previous = getattr(self, "_previous_frontend_sources", {})
        if not isinstance(previous, dict):
            raise ValueError("Invalid prior frontend source manifest")
        # Check all destinations before replacing any installed skeleton.
        for relative, source in sources.items():
            target = (workspace / relative).resolve()
            if workspace not in target.parents:
                raise ValueError(f"Frontend source escapes workspace: {relative}")
            if target.is_file():
                current = target.read_text(encoding="utf-8")
                if relative in previous and current != source and current != previous[relative].replace("\r\n", "\n"):
                    raise ValueError(f"Existing frontend source has implementation changes: {relative}")
                if relative not in previous and current != source and relative.startswith(
                    ("frontend/src/components/", "frontend/src/pages/", "frontend/src/functions/", "frontend/src/api/", "frontend/src/runtime/")
                ):
                    raise ValueError(f"Frontend source is not owned by this lowering: {relative}")
        retiring = []
        current_paths = {str((workspace / relative).resolve()).casefold() for relative in sources}
        for relative, old_content in previous.items():
            if relative in sources or not relative.startswith("frontend/src/"):
                continue
            target = (workspace / relative).resolve()
            if str(target).casefold() in current_paths:
                continue
            if not target.is_relative_to(frontend_root):
                raise ValueError(f"Retired frontend source escapes source directory: {relative}")
            if not target.is_file():
                continue
            if not isinstance(old_content, str) or target.read_text(encoding="utf-8") != old_content.replace("\r\n", "\n"):
                raise ValueError(f"Obsolete generated file has local edits; preserve or move it before lowering: {relative}")
            retiring.append((relative, target))
        artifacts = self.write_generated_sources(sources)
        archive = self.frontend_lowering_root / "retired" / uuid.uuid4().hex
        for relative, target in retiring:
            backup = archive / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup)
            artifacts[f"retired_source:{relative}"] = str(backup)
        self._previous_frontend_sources = dict(sources)
        write_json_atomic(self.frontend_lowering_root / "installed-sources.json", sources)
        return artifacts

    def read_project_manifest(self) -> tuple[dict[str, Any] | None, str | None]:
        """Validate the project-initialization boundary before compilation continues."""

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


    def write_code_bindings(self, registry: dict[str, Any]) -> str:
        path = self.lowering_root / "code_bindings.json"
        write_json_atomic(path, registry)
        return str(path)


    def write_test_manifest(self, manifest: dict[str, Any]) -> str:
        path = self.tests_root / "test_manifest.json"
        write_json_atomic(path, manifest)
        return str(path)


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
                or not (
                    normalized.endswith((".ts", ".tsx"))
                    or normalized in {
                        "backend/sql/schema.sqlite.sql",
                    }
                )
            ):
                raise ValueError(f"Invalid generated source path: {relative!r}")
            target = (output_root / Path(normalized)).resolve()
            if output_root != target and output_root not in target.parents:
                raise ValueError(f"Generated source escapes output workspace: {relative!r}")
            if not (
                normalized.startswith("backend/src/")
                or normalized in {
                    "backend/sql/schema.sqlite.sql",
                }
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
