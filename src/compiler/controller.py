from __future__ import annotations

import inspect
from pathlib import Path
from typing import Awaitable, Callable

from arcbench_agent_runtime.runtime import AgentRuntime

from .artifacts import CompilerArtifactStore
from .frontend import RequirementFrontend
from .models import CompilationRequest, CompilationResult, Diagnostic


LogCallback = Callable[[str, str, str | None, str | None], Awaitable[None] | None]


class Compiler:
    """Compile requirements through deterministic, validated passes."""

    def __init__(self, runtime: AgentRuntime, log_cb: LogCallback) -> None:
        self._runtime = runtime
        self._log_cb = log_cb
        self._frontend = RequirementFrontend()

    async def compile(self, request: CompilationRequest) -> CompilationResult:
        await self._log("Compiler", "Running deterministic FRONTEND pass.")
        frontend = self._frontend.compile(request.requirement_path)
        root_id = frontend.requirement_ir.get("root_id") if frontend.requirement_ir else None
        atomic_ids = list(frontend.requirement_ir.get("atomic_units", [])) if frontend.requirement_ir else []
        states = {node_id: ("DISCOVERED" if frontend.ok else "FAILED") for node_id in atomic_ids}

        artifact_store = CompilerArtifactStore(request.output_dir)
        artifacts = artifact_store.write_frontend(
            requirement_ir=frontend.requirement_ir,
            dependency_graph=frontend.dependency_graph,
            diagnostics=frontend.diagnostics,
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

        for diagnostic in frontend.diagnostics:
            await self._log("Compiler", f"{diagnostic.code}: {diagnostic.message}", diagnostic.severity, diagnostic.node_id)

        if not frontend.ok:
            await self._log("Compiler", "FRONTEND pass failed.", "error")
            return CompilationResult(
                ok=False,
                complete=False,
                root_id=root_id,
                states=states,
                failed_nodes=atomic_ids,
                diagnostics=frontend.diagnostics,
                artifacts=artifacts,
            )

        pending = Diagnostic(
            "ARC2000",
            "Requirement front end completed; discovery, design, lowering, implementation, and acceptance passes are not implemented yet.",
            severity="info",
        )
        await self._log("Compiler", pending.message, "warning")
        return CompilationResult(
            ok=True,
            complete=False,
            root_id=root_id,
            states=states,
            diagnostics=[*frontend.diagnostics, pending],
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
