from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Awaitable, Callable

from arcbench_agent_runtime.runtime import AgentRuntime

from .artifacts import CompilerArtifactStore
from .database_pass import (
    DatabasePassResult,
    DatabaseSchemaPass,
    database_structure,
    database_traceability,
    hydrate_database_schema,
    validate_database_schema,
    validate_database_structure,
)
from .design_pass import DesignPass, database_hash, design_hash
from .frontend import RequirementFrontend
from .model_client import Model, ModelConfigurationError, StructuredModel
from .models import CompilationRequest, CompilationResult


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
        self._frontend = RequirementFrontend()
        self._model = model

    async def compile(self, request: CompilationRequest) -> CompilationResult:

        # ===================================================================
        #                    Compiler Frontend Pass
        # ===================================================================

        await self._log("Compiler", "Running deterministic FRONTEND pass.")
        frontend = self._frontend.compile(request.requirement_path)
        root_id = frontend.requirement_ir.get("root_id") if frontend.requirement_ir else None
        atomic_ids = list(frontend.requirement_ir.get("atomic_units", [])) if frontend.requirement_ir else []
        states = {node_id: ("DISCOVERED" if frontend.ok else "FAILED") for node_id in atomic_ids}

        artifact_store = CompilerArtifactStore(request.output_dir)
        artifacts = artifact_store.write_frontend(
            requirement_ir=frontend.requirement_ir,
            dependency_graph=frontend.dependency_graph,
        )
        artifacts["processing_queue"] = artifact_store.write_queue(
            root_id=root_id,
            node_states=states,
            frontend_ok=frontend.ok,
        )

        if frontend.normalized_tree:
            self._runtime.traceability.store_requirement_tree(frontend.normalized_tree)
        for node_id, state in states.items():
            self._runtime.traceability.upsert_node_state(node_id, state, "frontend")

        for error in frontend.errors:
            await self._log("Compiler", error, "error")

        if not frontend.ok:
            await self._log("Compiler", "FRONTEND pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                failed_nodes=atomic_ids,
                artifacts=artifacts,
            )

        # ===================================================================
        #                    Compiler Database Pass
        # ===================================================================

        if request.skip_database:
            await self._log("Compiler", "Skipping DATABASE_SCHEMA pass; reusing existing database schema.")
            existing_schema, read_error = artifact_store.read_database()
            if read_error:
                validation_errors = [read_error]
                reusable_schema = existing_schema or {}
            elif isinstance(existing_schema, dict) and "traceability" in existing_schema:
                # Read schema_version 1 and early schema_version 2 artifacts during migration.
                reusable_schema = existing_schema
                validation_errors = validate_database_schema(reusable_schema)
            else:
                validation_errors = validate_database_structure(existing_schema or {})
                links = self._runtime.traceability.read_database_schema_links_from_requirements()
                reusable_schema = hydrate_database_schema(existing_schema or {}, links)
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
                    frontend_ok=True,
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
            artifacts["database_schema"] = str(artifact_store.root / "database_schema.json")
            artifacts["database_traceability"] = str(
                self._runtime.traceability.table_path("database_schema")
            )
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                frontend_ok=True,
                database_status="REUSED",
            )
            for node_id, state in database.node_states.items():
                self._runtime.traceability.upsert_node_state(node_id, state, "database_schema")
        else:
            await self._log(
                "Compiler",
                "Running ENTITY, FIELD, RELATIONSHIP, and CONSTRAINT database passes.",
            )
            database = None
        try:
            model = self._model or Model.from_env()
        except ModelConfigurationError as exc:
            await self._log("Compiler", str(exc), "error")
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                frontend_ok=True,
                database_status="REUSED" if request.skip_database else "FAILED",
            )
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                failed_nodes=atomic_ids,
                artifacts=artifacts,
            )

        if database is None:
            database_pass = DatabaseSchemaPass(model, artifact_store.root)
            database = await asyncio.to_thread(
                database_pass.compile,
                frontend.requirement_ir,
                frontend.dependency_graph,
                resume=request.resume,
            )
            states.update(database.node_states)
            artifacts.update(database.pass_artifacts)
            structure = database_structure(database.schema)
            links = database_traceability(database.schema)
            artifacts.update(artifact_store.write_database(schema=structure))
            self._runtime.traceability.merge_database_schema_links(links)
            database_status = "COMPLETED" if database.ok else "FAILED"
            artifacts["processing_queue"] = artifact_store.write_pass_queue(
                root_id=root_id,
                node_states=states,
                frontend_ok=True,
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

        # ===================================================================
        #                    Compiler Design Pass
        # ===================================================================

        await self._log(
            "Compiler",
            "Running REQUIREMENT_CONTRACT, MODULE_CALL_TREE, and FLOW_BINDING passes.",
        )
        design_pass = DesignPass(model, artifact_store.root)
        design = await asyncio.to_thread(
            design_pass.compile,
            frontend.requirement_ir,
            frontend.dependency_graph,
            database.schema,
            resume=request.resume,
        )
        states.update(design.node_states)
        artifacts.update(
            artifact_store.write_design(
                design_ir=design.design_ir,
                design_sha256=design_hash(design.design_ir) if design.ok else None,
                database_sha256=database_hash(database.schema) if design.ok else None,
            )
        )
        artifacts["processing_queue"] = artifact_store.write_pass_queue(
            root_id=root_id,
            node_states=states,
            frontend_ok=True,
            database_status="REUSED" if request.skip_database else "COMPLETED",
            design_status="COMPLETED" if design.ok else "FAILED",
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

        await self._log(
            "Compiler",
            "Design IR completed and frozen; lowering, implementation, and acceptance passes are pending.",
            "warning",
        )
        return CompilationResult(
            ok=True,
            complete=False,
            root_id=root_id,
            states=states,
            artifacts=artifacts,
        )

        await self._log(
            "Compiler",
            "Database schema completed; the DESIGN pass is currently disabled.",
            "warning",
        )
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
