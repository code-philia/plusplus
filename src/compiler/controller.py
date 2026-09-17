from __future__ import annotations

import inspect
from pathlib import Path
from typing import Awaitable, Callable

from arcbench_agent_runtime.runtime import AgentRuntime

from .artifacts import CompilerArtifactStore
from .backend_lowering import BackendGlueLowerer
from .database_stage import (
    DatabasePassResult,
    DatabaseSchemaPass,
    database_artifact_tables,
    database_structure,
    database_traceability,
    hydrate_database_schema,
    validate_database_schema,
    validate_database_structure,
)
from .design_stage import DesignPass, DesignPassResult, design_traceability
from .file_planning import GlobalFilePlanner
from .preprocessing_stage import RequirementPreprocessor
from .model_client import Model, ModelConfigurationError, StructuredModel
from .models import CompilationRequest, CompilationResult
from .module_lowering import ModuleSkeletonLowerer
from .project_build import ProjectBuilder
from .project_initialization import ProjectInitializer
from .skeleton_lowering import DatabaseSchemaLowerer, TypeLowerer
from .symbol_planning import GlobalSymbolPlanner


LogCallback = Callable[[str, str, str | None, str | None], Awaitable[None] | None]


class Compiler:
    """Compile requirements through deterministic, validated passes."""

    def __init__(
        self,
        runtime: AgentRuntime,
        log_cb: LogCallback,
        model: StructuredModel | None = None,
    ) -> None:
        self._runtime = runtime
        self._log_cb = log_cb
        self._preprocessor = RequirementPreprocessor()
        self._model = model

    async def compile(self, request: CompilationRequest) -> CompilationResult:
        stage_order = {
            "PREPROCESSING": 0,
            "DATABASE": 1,
            "DESIGN": 2,
            "PROJECT": 3,
            "SKELETON": 4,
        }
        start_from = str(request.start_from or "PREPROCESSING").strip().upper()
        if start_from not in stage_order:
            await self._log("Compiler", f"Unknown start stage: {start_from}", "error")
            return CompilationResult(ok=False, complete=False)
        start_rank = stage_order[start_from]
        database_reused = start_rank > stage_order["DATABASE"]
        design_reused = start_rank > stage_order["DESIGN"]
        project_reused = start_rank > stage_order["PROJECT"]

        # ===================================================================
        #                    Requirement Preprocessing Stage
        # ===================================================================

        await self._log(
            "Compiler",
            "Running deterministic PREPROCESSING pass."
            if start_from == "PREPROCESSING"
            else f"START_PROBE stage={start_from} upstream=PREPROCESSING source=requirements status=VALIDATING",
        )
        preprocessing = self._preprocessor.compile(request.requirement_path)
        root_id = preprocessing.requirement_ir.get("root_id") if preprocessing.requirement_ir else None
        atomic_ids = list(preprocessing.requirement_ir.get("atomic_units", [])) if preprocessing.requirement_ir else []
        states = {node_id: ("DISCOVERED" if preprocessing.ok else "FAILED") for node_id in atomic_ids}

        artifact_store = CompilerArtifactStore(request.output_dir)
        artifacts: dict[str, str] = {}
        if start_from == "PREPROCESSING":
            artifacts.update(artifact_store.write_preprocessing(
                requirement_ir=preprocessing.requirement_ir,
                dependency_graph=preprocessing.dependency_graph,
            ))
            artifacts["processing_queue"] = artifact_store.write_queue(
                root_id=root_id,
                node_states=states,
                preprocessing_ok=preprocessing.ok,
            )

        if preprocessing.normalized_tree:
            self._runtime.traceability.store_requirement_tree(preprocessing.normalized_tree)
        for node_id, state in states.items():
            self._runtime.traceability.upsert_node_state(node_id, state, "preprocessing")

        for error in preprocessing.errors:
            await self._log("Compiler", error, "error")

        if not preprocessing.ok:
            await self._log("Compiler", "PREPROCESSING pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                failed_nodes=atomic_ids,
                artifacts=artifacts,
            )
        if start_from != "PREPROCESSING":
            await self._log(
                "Compiler",
                f"START_PROBE stage={start_from} upstream=PREPROCESSING source=requirements status=VALIDATED",
            )

        # ===================================================================
        #                    Compiler Database Stage
        # ===================================================================

        model: StructuredModel | None = self._model
        if database_reused:
            await self._log(
                "Compiler",
                f"START_PROBE stage={start_from} upstream=DATABASE source=.arc/database status=VALIDATING",
            )
            existing_structure, read_error = artifact_store.read_database()
            if read_error:
                validation_errors = [read_error]
                reusable_schema = {}
            else:
                structure = existing_structure or {}
                validation_errors = validate_database_structure(structure)
                links = self._runtime.traceability.read_database_schema_links_from_requirements()
                reusable_schema = hydrate_database_schema(structure, links)
                if not validation_errors:
                    validation_errors = validate_database_schema(
                        reusable_schema,
                        expected_requirement_ids=set(atomic_ids),
                    )
            if validation_errors:
                for error in validation_errors:
                    await self._log("Compiler", f"ARC2104: Cannot reuse database schema: {error}", "error")
                artifacts["processing_queue"] = artifact_store.write_pass_queue(
                    root_id=root_id,
                    node_states=states,
                    preprocessing_ok=True,
                    database_status="FAILED",
                )
                return CompilationResult(
                    ok=False,
                    complete=False,
                    root_id=root_id,
                    states=states,
                    failed_nodes=atomic_ids,
                    artifacts=artifacts,
                )
            database = DatabasePassResult(
                schema=reusable_schema,
                node_states={node_id: "SCHEMA_REUSED" for node_id in atomic_ids},
            )
            states.update(database.node_states)
            artifacts["database_schema"] = str(artifact_store.database_root / "database_schema.json")
            artifacts["database_relationships"] = str(artifact_store.database_root / "relationships.json")
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                preprocessing_ok=True,
                database_status="REUSED",
            )
            for node_id, state in database.node_states.items():
                self._runtime.traceability.upsert_node_state(node_id, state, "database_schema")
            await self._log(
                "Compiler",
                f"START_PROBE stage={start_from} upstream=DATABASE source=.arc/database status=VALIDATED",
            )
        else:
            await self._log(
                "Compiler",
                "Running ENTITY, FIELD, RELATIONSHIP, and CONSTRAINT database passes.",
            )
            database = None

        if database is None:
            try:
                model = model or Model.from_env()
            except ModelConfigurationError as exc:
                await self._log("Compiler", str(exc), "error")
                artifacts["processing_queue"] = artifact_store.write_pass_queue(
                    root_id=root_id,
                    node_states=states,
                    preprocessing_ok=True,
                    database_status="FAILED",
                )
                return CompilationResult(
                    ok=False,
                    complete=False,
                    root_id=root_id,
                    states=states,
                    failed_nodes=atomic_ids,
                    artifacts=artifacts,
                )
            database_stage = DatabaseSchemaPass(model, artifact_store.root)
            # Stage 1 is deliberately synchronous: each database pass and each
            # requirement completes before the next one starts.
            database = database_stage.compile(
                preprocessing.requirement_ir,
                preprocessing.dependency_graph,
                resume=request.resume,
            )
            states.update(database.node_states)
            artifacts.update(database.pass_artifacts)
            structure = database_structure(database.schema)
            links = database_traceability(database.schema)
            database_status = "COMPLETED" if database.ok else "FAILED"
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                preprocessing_ok=True,
                database_status=database_status,
            )
            for node_id, state in database.node_states.items():
                self._runtime.traceability.upsert_node_state(node_id, state, "database_schema")
            for error in database.errors:
                await self._log("Compiler", error, "error")
            if not database.ok:
                failed_nodes = sorted(node_id for node_id, state in states.items() if state == "FAILED")
                await self._log("Compiler", "DATABASE_SCHEMA pass failed.", "error")
                return CompilationResult(
                    ok=False,
                    complete=False,
                    root_id=root_id,
                    states=states,
                    failed_nodes=failed_nodes,
                    artifacts=artifacts,
                )
            entities, relationships = database_artifact_tables(structure)
            artifacts.update(artifact_store.write_database(
                entities=entities,
                relationships=relationships,
            ))
            self._runtime.traceability.merge_database_schema_links(links)

        # ===================================================================
        #                    Compiler Design Stage
        # ===================================================================

        design_stop_after = "MODULES"
        if design_reused:
            await self._log(
                "Compiler",
                f"START_PROBE stage={start_from} upstream=DESIGN source=.arc/design status=VALIDATING",
            )
            reusable_design, read_error = artifact_store.read_design(
                expected_requirement_ids=set(atomic_ids),
            )
            if read_error:
                await self._log("Compiler", f"ARC3104: Cannot reuse Design IR: {read_error}", "error")
                artifacts["processing_queue"] = artifact_store.write_pass_queue(
                    root_id=root_id,
                    node_states=states,
                    preprocessing_ok=True,
                    database_status="REUSED" if database_reused else "COMPLETED",
                    design_status="FAILED",
                )
                return CompilationResult(
                    ok=False,
                    complete=False,
                    root_id=root_id,
                    states=states,
                    failed_nodes=atomic_ids,
                    artifacts=artifacts,
                )
            design = DesignPassResult(
                design_ir=reusable_design or {},
                node_states={node_id: "DESIGN_REUSED" for node_id in atomic_ids},
            )
            states.update(design.node_states)
            for name, filename in (
                ("design_requirement_contracts", "requirement_contracts.json"),
                ("design_api_modules", "api_modules.json"),
                ("design_function_modules", "function_modules.json"),
                ("design_db_modules", "db_modules.json"),
            ):
                artifacts[name] = str(artifact_store.design_root / filename)
            await self._log(
                "Compiler",
                f"START_PROBE stage={start_from} upstream=DESIGN source=.arc/design status=VALIDATED",
            )
        else:
            try:
                model = model or Model.from_env()
            except ModelConfigurationError as exc:
                await self._log("Compiler", str(exc), "error")
                return CompilationResult(
                    ok=False,
                    complete=False,
                    root_id=root_id,
                    states=states,
                    failed_nodes=atomic_ids,
                    artifacts=artifacts,
                )
            design_stage = DesignPass(model, artifact_store.root)
            design_stop_after = design_stage.stop_after
            if design_stop_after == "API":
                design_message = (
                    "Running REQUIREMENT CONTRACT and REQUIREMENT TO API passes; "
                    "Stage 2 stops after API generation."
                )
            elif design_stop_after == "FUNC":
                design_message = (
                    "Running REQUIREMENT CONTRACT, REQUIREMENT TO API, and API TO FUNC decomposition; "
                    "Stage 2 stops after direct FUNC generation."
                )
            else:
                design_message = (
                    "Running REQUIREMENT CONTRACT, REQUIREMENT TO API, and full top-down MODULE DECOMPOSITION passes."
                )
            await self._log("Compiler", design_message)
            # Stage 2 keeps contract generation and module materialization serial.
            design = design_stage.compile(
                preprocessing.requirement_ir,
                preprocessing.dependency_graph,
                database.schema,
            )
        states.update(design.node_states)
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status=("REUSED" if design_reused else "COMPLETED") if design.ok else "FAILED",
        )
        for node_id, state in design.node_states.items():
            self._runtime.traceability.upsert_node_state(node_id, state, "design")
        for error in design.errors:
            await self._log("Compiler", error, "error")
        if not design.ok:
            failed_nodes = sorted(node_id for node_id, state in states.items() if state == "FAILED")
            await self._log("Compiler", "DESIGN pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                failed_nodes=failed_nodes,
                artifacts=artifacts,
            )

        if not design_reused:
            artifacts.update(artifact_store.write_design(design_ir=design.design_ir))
            self._runtime.traceability.merge_design_links(design_traceability(design.design_ir))

        # ===================================================================
        #                    Project Initialization Stage
        # ===================================================================

        project_ok = True
        project_errors: list[str] = []
        if project_reused:
            await self._log(
                "Compiler",
                f"START_PROBE stage={start_from} upstream=PROJECT source=.arc/project/project-manifest.json status=VALIDATING",
            )
            _, project_error = artifact_store.read_project_manifest()
            if project_error:
                project_ok = False
                project_errors.append(f"ARC3201: Cannot reuse initialized project: {project_error}")
            else:
                artifacts["project_manifest"] = str(
                    artifact_store.root / "project" / "project-manifest.json"
                )
                await self._log(
                    "Compiler",
                    f"START_PROBE stage={start_from} upstream=PROJECT source=.arc/project/project-manifest.json status=VALIDATED",
                )
        else:
            await self._log(
                "Compiler",
                "Running deterministic PROJECT_INITIALIZATION with official ecosystem scaffolders.",
            )
            initializer = ProjectInitializer(
                request.output_dir,
                web_port=request.web_port,
            )
            project = initializer.initialize(request.app_type)
            project_ok = project.ok
            project_errors.extend(project.errors)
            artifacts.update(project.artifacts)
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status="REUSED" if design_reused else "COMPLETED",
            project_status=("REUSED" if project_reused else "COMPLETED") if project_ok else "FAILED",
        )
        for error in project_errors:
            await self._log("Compiler", error, "error")
        if not project_ok:
            await self._log("Compiler", "PROJECT_INITIALIZATION pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )
        await self._log(
            "Compiler",
            "Project workspace initialized; Skeleton lowering may consume the frozen project manifest.",
        )

        # ===================================================================
        #                  Skeleton Stage 3.1: Symbol Planning
        # ===================================================================

        await self._log(
            "Compiler",
            "Running deterministic GLOBAL_SYMBOL_PLANNING over Design IR and Database Schema IR.",
        )
        symbol_planning = GlobalSymbolPlanner(request.output_dir).plan(
            design.design_ir,
            database.schema,
        )
        artifacts["backend_symbol_registry"] = artifact_store.write_symbol_registry(
            symbol_planning.registry
        )
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status="COMPLETED",
            project_status="COMPLETED",
            lowering_status="SYMBOLS_PLANNED" if symbol_planning.ok else "FAILED",
        )
        for error in symbol_planning.errors:
            await self._log("Compiler", error, "error")
        if not symbol_planning.ok:
            await self._log("Compiler", "GLOBAL_SYMBOL_PLANNING pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )
        await self._log(
            "Compiler",
            "Global Symbol Registry planned.",
        )

        # ===================================================================
        #                   Skeleton Stage 3.1: File Planning
        # ===================================================================

        await self._log(
            "Compiler",
            "Running deterministic GLOBAL_FILE_PLANNING over Design IR and the Symbol Registry.",
        )
        file_planning = GlobalFilePlanner(request.output_dir).plan(
            design.design_ir,
            symbol_planning.registry,
        )
        artifacts["backend_file_registry"] = artifact_store.write_file_registry(
            file_planning.registry
        )
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status="COMPLETED",
            project_status="COMPLETED",
            lowering_status="FILES_PLANNED" if file_planning.ok else "FAILED",
        )
        for error in file_planning.errors:
            await self._log("Compiler", error, "error")
        if not file_planning.ok:
            await self._log("Compiler", "GLOBAL_FILE_PLANNING pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )
        await self._log(
            "Compiler",
            "Global File Registry planned.",
        )

        # ===================================================================
        #                    Skeleton Stage 3.1: Type Lowering
        # ===================================================================

        await self._log(
            "Compiler",
            "Running deterministic TYPE_LOWERING for canonical TypeScript definitions.",
        )
        type_lowering = TypeLowerer().lower(
            symbol_planning.registry,
            file_planning.registry,
        )
        artifacts["backend_type_manifest"] = artifact_store.write_type_manifest(
            type_lowering.manifest
        )
        for error in type_lowering.errors:
            await self._log("Compiler", error, "error")
        if not type_lowering.ok:
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                preprocessing_ok=True,
                database_status="REUSED" if database_reused else "COMPLETED",
                design_status="COMPLETED",
                project_status="COMPLETED",
                lowering_status="FAILED",
            )
            await self._log("Compiler", "TYPE_LOWERING pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )
        artifacts.update(artifact_store.write_generated_sources(type_lowering.sources))
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status="COMPLETED",
            project_status="COMPLETED",
            lowering_status="TYPES_GENERATED",
        )
        await self._log("Compiler", "Canonical TypeScript type world generated.")

        # ===================================================================
        #              Skeleton Stage 3.1: Database Schema Lowering
        # ===================================================================

        await self._log(
            "Compiler",
            "Running deterministic DATABASE_SCHEMA_LOWERING for SQLite and Drizzle.",
        )
        database_lowering = DatabaseSchemaLowerer().lower(
            database.schema,
            symbol_planning.registry,
            file_planning.registry,
        )
        artifacts["backend_database_schema_manifest"] = (
            artifact_store.write_database_schema_manifest(database_lowering.manifest)
        )
        for warning in database_lowering.warnings:
            await self._log("Compiler", warning, "warning")
        for error in database_lowering.errors:
            await self._log("Compiler", error, "error")
        if not database_lowering.ok:
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                preprocessing_ok=True,
                database_status="REUSED" if database_reused else "COMPLETED",
                design_status="COMPLETED",
                project_status="COMPLETED",
                lowering_status="FAILED",
            )
            await self._log("Compiler", "DATABASE_SCHEMA_LOWERING pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )
        artifacts.update(artifact_store.write_generated_sources(database_lowering.sources))
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status="COMPLETED",
            project_status="COMPLETED",
            lowering_status="DATABASE_SCHEMA_LOWERED",
        )
        await self._log(
            "Compiler",
            "SQLite/Drizzle schema lowered; DB Module Skeleton generation is next.",
        )

        # ===================================================================
        #                Skeleton Stage 3.1: DB Module Lowering
        # ===================================================================

        await self._log(
            "Compiler",
            "Running deterministic DB_MODULE_LOWERING from frozen registries.",
        )
        module_lowerer = ModuleSkeletonLowerer()
        db_modules = module_lowerer.lower(
            "DB",
            design.design_ir,
            symbol_planning.registry,
            file_planning.registry,
        )
        artifacts["backend_db_modules_manifest"] = artifact_store.write_module_manifest(
            "DB",
            db_modules.manifest,
        )
        for error in db_modules.errors:
            await self._log("Compiler", error, "error")
        if not db_modules.ok:
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                preprocessing_ok=True,
                database_status="REUSED" if database_reused else "COMPLETED",
                design_status="COMPLETED",
                project_status="COMPLETED",
                lowering_status="FAILED",
            )
            await self._log("Compiler", "DB_MODULE_LOWERING pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )
        artifacts.update(artifact_store.write_generated_sources(db_modules.sources))
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status="COMPLETED",
            project_status="COMPLETED",
            lowering_status="DB_MODULES_GENERATED",
        )
        await self._log("Compiler", "DB Module Skeletons generated.")

        # ===================================================================
        #               Skeleton Stage 3.1: FUNC Module Lowering
        # ===================================================================

        await self._log(
            "Compiler",
            "Running deterministic FUNC_MODULE_LOWERING from frozen registries.",
        )
        func_modules = module_lowerer.lower(
            "FUNC",
            design.design_ir,
            symbol_planning.registry,
            file_planning.registry,
        )
        artifacts["backend_func_modules_manifest"] = artifact_store.write_module_manifest(
            "FUNC",
            func_modules.manifest,
        )
        for error in func_modules.errors:
            await self._log("Compiler", error, "error")
        if not func_modules.ok:
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                preprocessing_ok=True,
                database_status="REUSED" if database_reused else "COMPLETED",
                design_status="COMPLETED",
                project_status="COMPLETED",
                lowering_status="FAILED",
            )
            await self._log("Compiler", "FUNC_MODULE_LOWERING pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )
        artifacts.update(artifact_store.write_generated_sources(func_modules.sources))
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status="COMPLETED",
            project_status="COMPLETED",
            lowering_status="FUNC_MODULES_GENERATED",
        )
        await self._log("Compiler", "FUNC Module Skeletons generated.")

        # ===================================================================
        #                Skeleton Stage 3.1: API Module Lowering
        # ===================================================================

        await self._log(
            "Compiler",
            "Running deterministic API_MODULE_LOWERING from frozen registries.",
        )
        api_modules = module_lowerer.lower(
            "API",
            design.design_ir,
            symbol_planning.registry,
            file_planning.registry,
        )
        artifacts["backend_api_modules_manifest"] = artifact_store.write_module_manifest(
            "API",
            api_modules.manifest,
        )
        for error in api_modules.errors:
            await self._log("Compiler", error, "error")
        if not api_modules.ok:
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                preprocessing_ok=True,
                database_status="REUSED" if database_reused else "COMPLETED",
                design_status="COMPLETED",
                project_status="COMPLETED",
                lowering_status="FAILED",
            )
            await self._log("Compiler", "API_MODULE_LOWERING pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )
        artifacts.update(artifact_store.write_generated_sources(api_modules.sources))
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status="COMPLETED",
            project_status="COMPLETED",
            lowering_status="API_MODULES_GENERATED",
        )
        await self._log(
            "Compiler",
            "API Module Skeletons generated; global Glue Code generation is next.",
        )

        # ===================================================================
        #          Skeleton Stage 3.1: Global Glue and Backend Manifest
        # ===================================================================

        await self._log(
            "Compiler",
            "Running deterministic GLOBAL_GLUE_LOWERING with Route and Import Planning.",
        )
        backend_glue = BackendGlueLowerer().lower(
            design.design_ir,
            symbol_planning.registry,
            file_planning.registry,
            {
                "type": type_lowering.manifest,
                "database": database_lowering.manifest,
                "DB": db_modules.manifest,
                "FUNC": func_modules.manifest,
                "API": api_modules.manifest,
            },
            default_port=request.web_port,
        )
        artifacts.update(
            artifact_store.write_backend_lowering(
                route_registry=backend_glue.route_registry,
                import_plan=backend_glue.import_plan,
                manifest=backend_glue.manifest,
            )
        )
        for error in backend_glue.errors:
            await self._log("Compiler", error, "error")
        if not backend_glue.ok:
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                preprocessing_ok=True,
                database_status="REUSED" if database_reused else "COMPLETED",
                design_status="COMPLETED",
                project_status="COMPLETED",
                lowering_status="FAILED",
            )
            await self._log("Compiler", "GLOBAL_GLUE_LOWERING pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )
        artifacts.update(artifact_store.write_generated_sources(backend_glue.sources))
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status="COMPLETED",
            project_status="COMPLETED",
            lowering_status="BACKEND_MANIFEST_GENERATED",
        )
        await self._log(
            "Compiler",
            "Global Glue Code, Route Registration, Barrel Export, Import Plan, and Backend Manifest generated.",
        )

        # ===================================================================
        #            Skeleton Stage 3.1: Synchronous Build Acceptance
        # ===================================================================

        await self._log(
            "Compiler",
            "Running synchronous PROJECT_BUILD acceptance gate with npm run build.",
        )
        project_build = ProjectBuilder(request.output_dir).build()
        for error in project_build.errors:
            await self._log("Compiler", error, "error")
        if not project_build.ok:
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                preprocessing_ok=True,
                database_status="REUSED" if database_reused else "COMPLETED",
                design_status="COMPLETED",
                project_status="COMPLETED",
                lowering_status="BACKEND_BUILD_FAILED",
            )
            await self._log("Compiler", "PROJECT_BUILD acceptance gate failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )

        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            preprocessing_ok=True,
            database_status="REUSED" if database_reused else "COMPLETED",
            design_status="COMPLETED",
            project_status="COMPLETED",
            lowering_status="BACKEND_BUILD_SUCCEEDED",
        )
        await self._log("Compiler", "PROJECT_BUILD acceptance gate completed successfully.")

        final_message = {
            "API": "Stage 2 API boundary completed; Backend Manifest generated and project build passed over the partial Design IR; later passes are pending.",
            "FUNC": "Stage 2 FUNC boundary completed; Backend Manifest generated and project build passed over the partial Design IR; later passes are pending.",
            "MODULES": "Whole-program backend Skeleton and Backend Manifest generated; project build passed and later passes are pending.",
        }[design_stop_after]

        await self._log("Compiler", final_message, "warning")
        return CompilationResult(
            ok=True,
            complete=False,
            root_id=root_id,
            states=states,
            artifacts=artifacts,
        )

    async def _log(
        self,
        agent_name: str,
        message: str,
        status: str | None = None,
        node_id: str | None = None,
    ) -> None:
        result = self._log_cb(agent_name, message, status, node_id)
        if inspect.isawaitable(result):
            await result
