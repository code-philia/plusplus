from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Awaitable, Callable

from arcbench_agent_runtime.runtime import AgentRuntime

from .artifacts import CompilerArtifactStore
from .database_pass import DatabaseSchemaPass
from .frontend import RequirementFrontend
from .model_client import ModelConfigurationError, OpenAIChatCompletionsModel, StructuredModel
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

        await self._log("Compiler", "Running DATABASE_SCHEMA pass over atomic requirements.")
        try:
            model = self._model or OpenAIChatCompletionsModel.from_env()
        except ModelConfigurationError as exc:
            await self._log("Compiler", str(exc), "error")
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

        database_pass = DatabaseSchemaPass(model, artifact_store.root)
        database = await asyncio.to_thread(
            database_pass.compile,
            frontend.requirement_ir,
            frontend.dependency_graph,
            resume=request.resume,
        )
        states.update(database.node_states)
        artifacts.update(
            artifact_store.write_database(schema=database.schema)
        )
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

        await self._log(
            "Compiler",
            "Database schema completed; design, lowering, implementation, and acceptance passes are not implemented yet.",
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
