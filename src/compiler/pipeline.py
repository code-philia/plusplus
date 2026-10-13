from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from typing import Any, Awaitable, Callable

from compiler.runtime.context import CompilationContext
from utils.logging import append_debug_log, write_terminal_log, set_run_id

from compiler.runtime.artifacts import CompilerArtifactStore
from compiler.preprocessing.stage import RequirementPreprocessor
from model.contracts import StructuredModel
from model.client import Model
from compiler.contracts import CompilationRequest, CompilationResult
from compiler.project_initialization.stage import (
    ProjectInitializer,
    validate_frontend_environment,
)
from compiler.preprocessing.visual import (
    VisualReferenceAnalyzer, VisualReferenceResolver,
)


LogCallback = Callable[[str, str, str | None, str | None], Awaitable[None] | None]


async def _default_log_cb(agent_name: str, message: str, status: str | None = None,
                          node_id: str | None = None) -> None:
    append_debug_log(agent_name, message, status=status, node_id=node_id)
    write_terminal_log(agent_name, message, status=status, node_id=node_id)


class Compiler:
    """Implement requirements directly without design IR or lowering."""

    def __init__(
        self,
        runtime: CompilationContext | None = None,
        log_cb: LogCallback | None = None,
        model: StructuredModel | None = None,
    ) -> None:
        self._runtime = runtime
        self._configured_runtime = runtime
        self._log_cb = log_cb or _default_log_cb
        self._preprocessor = RequirementPreprocessor()
        self._model = model

    async def compile(self, request: CompilationRequest) -> CompilationResult:
        if request.start_from != "zero" or request.stop_after_lowering:
            raise ValueError("Direct-source compilation has no design/lowering checkpoints; use --resume")
        from compiler.validation_preparation import ValidationPreparationPass, disable_validation_startup
        from compiler.validation_preparation.stage import check_validation_readiness
        # A previously prepared deployment must not seed during internal TDD.
        disable_validation_startup(request.output_dir.expanduser().resolve())
        run_id = uuid.uuid4().hex
        set_run_id(run_id)
        os.environ["ARC_WORKSPACE_ROOT"] = str(request.output_dir.expanduser().resolve())
        runtime = self._configured_runtime or CompilationContext.for_project(request.output_dir, run_id=run_id)
        if runtime.paths.project_dir != request.output_dir.expanduser().resolve():
            raise ValueError("Compilation context and output directory must refer to the same project")
        self._runtime = runtime
        runtime.events.run_id = run_id
        runtime.traceability.init_store()
        runtime.events.mark_run_started("ARC compilation started.", phase="PROJECT")
        await self._log("Compiler", "ARC compilation started.")
        result = await self._run_compile(request)
        if request.prepare_validation and result.ok:
            try:
                await self._log("Compiler", "Checking application build and startup before validation seed preparation.")
                await asyncio.to_thread(check_validation_readiness, request.output_dir)
                await self._log("Compiler", "Application readiness passed; generating external validation seed code.")
                model = self._model or Model.from_env()
                if hasattr(model, "set_usage_path"):
                    model.set_usage_path(request.output_dir / ".arc/model_usage.jsonl")
                requirement_ir = json.loads((request.output_dir / ".arc/preprocessing/requirement_ir.json").read_text(encoding="utf-8"))
                generated = await asyncio.to_thread(ValidationPreparationPass(model, request.output_dir).generate, requirement_ir)
                result.artifacts.update(generated)
            except Exception as exc:
                result.ok = False
                await self._log("Compiler", f"Validation preparation failed: {exc}", "error")
        result.run_id = run_id
        if result.ok:
            if result.failed_nodes:
                runtime.events.mark_run_completed("ARC compilation completed with skipped requirements.")
                await self._log("Compiler", f"Compilation finished with skipped requirements: {sorted(result.failed_nodes)}.", "warning")
            else:
                runtime.events.mark_run_completed("ARC compilation completed.")
                await self._log("Compiler", "Compilation finished successfully.")
        else:
            runtime.events.mark_run_failed("ARC compiler pass failed.")
            await self._log("Compiler", "Compilation failed; inspect the compiler log.", "error")
        return result

    async def _run_compile(self, request: CompilationRequest) -> CompilationResult:
        try:
            return await self._compile(request)
        except Exception as exc:
            # A malformed model result, optional visual analysis failure, or an
            # unexpected design-pass exception must become a compilation result,
            # not an uncaught process-level exception.
            detail = f"{type(exc).__name__}: {exc}" or type(exc).__name__
            await self._log("Compiler", f"ARC1000 COMPILATION_UNHANDLED: {detail}", "error")
            return CompilationResult(ok=False)

    async def _compile(self, request: CompilationRequest) -> CompilationResult:
        from compiler.implementation.direct import DirectImplementation
        from utils.jsonio import write_json_atomic

        if request.start_from != "zero" or request.stop_after_lowering:
            raise ValueError("Design/lowering checkpoints are unavailable in the direct-source compiler; use --resume")
        root = request.output_dir.expanduser().resolve()
        if any((root / relative).exists() for relative in ('.arc/design/database/schema.json',
            '.arc/design/backend/backend_ir.json', '.arc/design/frontend/frontend_ir.json', '.arc/lowering')):
            raise ValueError('Direct-source ablation requires a workspace without prior design/lowering artifacts')
        store = CompilerArtifactStore(root)
        artifacts: dict[str, str] = {}
        manifest_path = root / ".arc/project/project-manifest.json"
        if not manifest_path.is_file():
            if request.resume:
                raise ValueError("Resume requires an initialized direct-source workspace")
            await self._log("Compiler", "Initializing the application template.")
            project = await asyncio.to_thread(ProjectInitializer(root, web_port=request.web_port).initialize)
            artifacts.update(project.artifacts)
            if not project.ok:
                for error in project.errors:
                    await self._log("Compiler", error, "error")
                return CompilationResult(ok=False, artifacts=artifacts)
        manifest = json.loads(manifest_path.read_text())
        errors = validate_frontend_environment(root, manifest)
        if errors:
            for error in errors:
                await self._log("Compiler", error, "error")
            return CompilationResult(ok=False, artifacts=artifacts)
        artifacts["project_manifest"] = str(manifest_path)
        preprocessing = self._preprocessor.compile(request.requirement_path)
        artifacts.update(store.write_preprocessing(requirement_ir=preprocessing.requirement_ir,
                                                   dependency_graph=preprocessing.dependency_graph))
        if not preprocessing.ok:
            for error in preprocessing.errors:
                await self._log("Compiler", error, "error")
            return CompilationResult(ok=False, artifacts=artifacts)
        nodes = preprocessing.requirement_ir["nodes"]
        self._runtime.traceability.store_requirement_ids(list(nodes))
        checkpoint = root / ".arc/implementation/direct_workspace.json"
        source_hash = preprocessing.requirement_ir["source"]["sha256"]
        if request.resume:
            previous = json.loads(checkpoint.read_text())
            if previous.get("mode") != "direct-source" or previous.get("requirement_sha256") != source_hash:
                raise ValueError("Resume requires the same requirements and a direct-source workspace")
            if previous.get("web_port") != request.web_port:
                raise ValueError("Resume port differs from the original run")
        else:
            if (root / ".arc/implementation/direct_progress.json").is_file():
                raise ValueError("Direct implementation already exists; use --resume or a new workspace")
            write_json_atomic(checkpoint, {"mode": "direct-source", "requirement_sha256": source_hash,
                                           "web_port": request.web_port})
        model = self._model or Model.from_env()
        if hasattr(model, "set_usage_path"):
            model.set_usage_path(store.root / "model_usage.jsonl")
        resolved = VisualReferenceResolver().resolve(request.requirement_path, preprocessing.requirement_ir)
        visuals = [row.analysis_payload() for row in resolved.references]
        for issue in resolved.errors:
            await self._log("Compiler", issue.format(), "warning")
        if resolved.references:
            await self._log("Compiler", "Loading reference image analyses for direct source implementation.")
            try:
                analyzed = await asyncio.to_thread(VisualReferenceAnalyzer.from_env(store.root).analyze_cached,
                    resolved.references, store.root / "preprocessing/visual_cache.json")
                by_id = {row.get("id", row.get("reference_id")): row for row in analyzed.references}
                for row in visuals:
                    row["id"] = row["reference_id"]
                    row["analysis"] = by_id.get(row["reference_id"], {}).get("analysis", {})
                for issue in analyzed.errors:
                    await self._log("Compiler", issue.format(), "warning")
            except Exception as exc:
                await self._log("Compiler", f"Image analysis unavailable: {exc}", "warning")
        await self._log("Compiler", "Implementing requirements directly from source, without design IR, lowering or code binding.")
        direct = DirectImplementation(model, root, preprocessing.requirement_ir, preprocessing.dependency_graph,
            visuals=visuals, iterations=max(1, min(int(os.environ.get("ARC_TDD_MAX_ITERATIONS_PER_LAYER", "5")), 50)))
        outcome = await asyncio.to_thread(direct.run, resume=request.resume)
        for error in outcome["errors"]:
            await self._log("Compiler", error, "error")
        artifacts["implementation_progress"] = outcome["progress"]
        write_json_atomic(root / ".arc/implementation/direct_report.json", outcome)
        artifacts["implementation_report"] = str(root / ".arc/implementation/direct_report.json")
        return CompilationResult(ok=outcome["ok"], root_id=preprocessing.requirement_ir.get("root_id"),
            states=outcome["states"], failed_nodes=outcome["failed_nodes"], artifacts=artifacts)

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
