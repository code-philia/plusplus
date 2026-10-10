from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from compiler.runtime.context import CompilationContext
from utils.logging import append_debug_log, write_terminal_log, set_run_id

from compiler.runtime.artifacts import CompilerArtifactStore
from compiler.runtime.checkpoints import CheckpointStore, START_FROM
from compiler.backend_lowering import lower_backend
from compiler.code_binding import CodeTargetResolver, CodeBindingLowerer
from compiler.database_design import (
    DatabaseSchemaPass,
    database_traceability,
)
from compiler.database_lowering import lower_database
from compiler.backend_design import BackendDesignPass, design_traceability
from compiler.backend_lowering.projection import project_lowering_design
from compiler.frontend_design import FrontendIRGenerationPass, frontend_ir_traceability
from compiler.frontend_lowering import FrontendReactLowerer
from compiler.runtime.history import GitStageError, ProjectGitHistory
from compiler.preprocessing.traversal import postorder
from compiler.preprocessing.stage import RequirementPreprocessor, PreprocessingResult
from model.contracts import StructuredModel
from model.client import Model, ModelConfigurationError
from compiler.runtime.snapshots import stage_checkpoint
from compiler.contracts import CompilationRequest, CompilationResult
from compiler.build.stage import ProjectBuilder
from compiler.project_initialization.stage import (
    ProjectInitializer,
    validate_frontend_environment,
)
from compiler.implementation.stage import NodeTDDOrchestrator
from compiler.implementation.progress import TDDProgress, load_tdd_manifest
from compiler.implementation.test_generation import RequirementTestGenerationPass, TestEnvironmentInitializer
from compiler.implementation.test_runner import TestRunner
from compiler.preprocessing.visual import (
    VisualReferenceAnalyzer, VisualReferenceAnalysisResult, VisualReferenceResolver,
)


LogCallback = Callable[[str, str, str | None, str | None], Awaitable[None] | None]


async def _default_log_cb(agent_name: str, message: str, status: str | None = None,
                          node_id: str | None = None) -> None:
    append_debug_log(agent_name, message, status=status, node_id=node_id)
    write_terminal_log(agent_name, message, status=status, node_id=node_id)


def _tdd_postorder(requirement_ir: dict[str, Any]) -> list[str]:
    """Use the same document-order DFS postorder as all design passes."""
    targets = set(requirement_ir.get("atomic_units", [])) | set(requirement_ir.get("folder_nodes", []))
    order = postorder(requirement_ir)
    if missing := targets - set(order):
        raise ValueError(f"Requirements unreachable from root: {sorted(missing)}")
    return [node_id for node_id in order if node_id in targets]


class Compiler:
    """Compile requirements through deterministic, validated passes."""

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
        visual_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="arc-visual-analysis")
        try:
            return await self._compile(request, visual_executor)
        except GitStageError as exc:
            await self._log("Compiler", str(exc), "error")
            return CompilationResult(ok=False)
        except Exception as exc:
            # A malformed model result, optional visual analysis failure, or an
            # unexpected design-pass exception must become a compilation result,
            # not an uncaught process-level exception.
            detail = f"{type(exc).__name__}: {exc}" or type(exc).__name__
            await self._log("Compiler", f"ARC1000 COMPILATION_UNHANDLED: {detail}", "error")
            return CompilationResult(ok=False)
        finally:
            await asyncio.to_thread(visual_executor.shutdown, wait=True)

    async def _compile(self, request: CompilationRequest,
                       visual_executor: ThreadPoolExecutor) -> CompilationResult:
        artifact_store = CompilerArtifactStore(request.output_dir)
        artifacts: dict[str, str] = {}
        history = ProjectGitHistory(request.output_dir)
        if request.start_from not in START_FROM:
            raise GitStageError(f"Unknown start_from: {request.start_from}")
        if request.resume and request.start_from != "zero":
            raise GitStageError("resume and start_from are mutually exclusive")
        rank = 5 if request.resume else START_FROM.index(request.start_from)
        checkpoints = CheckpointStore(request.output_dir, request.requirement_path, request.web_port)
        saved = checkpoints.load("lowered" if request.resume else request.start_from, resume=request.resume) if rank else {}
        if rank:
            artifacts["checkpoint"] = str(request.output_dir / ".arc/checkpoints/current.json")
            for name, relative in {
                "project_manifest": ".arc/project/project-manifest.json",
                "database_schema": ".arc/design/database/schema.json",
                "database_er_model": ".arc/design/database/er_model.json",
                "database_schema_ir": ".arc/design/database/schema.json",
                "database_lowering_report": ".arc/lowering/database/report.json",
                "database_lowering_manifest": ".arc/lowering/database/manifest.json",
                "design_ir": ".arc/design/backend/backend_ir.json", "frontend_design_ir": ".arc/design/frontend/frontend_ir.json",
                "backend_ir": ".arc/design/backend/backend_ir.json",
                "backend_design_report": ".arc/design/backend/report.json",
                "frontend_traceability": ".arc/design/frontend/requirements.json",
                "frontend_lowering_report": ".arc/lowering/frontend/lowering.json",
            }.items():
                if (request.output_dir / relative).is_file():
                    artifacts[name] = str(request.output_dir / relative)
            await self._log("Compiler", "Resuming unstarted TDD requirements in the existing project."
                            if request.resume else f"Starting after completed checkpoint: {request.start_from}")

        if rank == 0:
            self._runtime.events.mark_phase_started("PROJECT", "Initializing generated project.")
            project = ProjectInitializer(request.output_dir, web_port=request.web_port).initialize()
            artifacts.update(project.artifacts)
            if not project.ok:
                for error in project.errors:
                    await self._log("Compiler", error, "error")
                return CompilationResult(ok=False, artifacts=artifacts)
        project_manifest, project_error = artifact_store.read_project_manifest()
        if project_error or project_manifest is None:
            await self._log("Compiler", project_error or "Missing project manifest.", "error")
            return CompilationResult(ok=False, artifacts=artifacts)
        frontend_errors = validate_frontend_environment(request.output_dir, project_manifest)
        if frontend_errors:
            for error in frontend_errors:
                await self._log("Compiler", error, "error")
            return CompilationResult(ok=False, artifacts=artifacts)
        if rank == 0:
            try:
                history.initialize()
                history.commit("0 project initialization", [
                    ".gitignore", ".env.example", "README.md", "package.json", "package-lock.json",
                    "backend", "frontend", "shared", "tests", ".arc/project",
                ])
            except GitStageError as exc:
                await self._log("Compiler", str(exc), "error")
                return CompilationResult(ok=False, artifacts=artifacts)

        if rank == 0:
            checkpoints.save("initialized")

        # ===================================================================
        #                    Requirement Preprocessing Stage
        # ===================================================================

        await self._log(
            "Compiler",
            "Running deterministic PREPROCESSING pass." if rank <= 1 else "Restoring checkpoint preprocessing artifacts.",
        )
        preprocessing = (self._preprocessor.compile(request.requirement_path) if rank <= 1 else
                         PreprocessingResult(**saved["preprocessing"]))
        root_id = preprocessing.requirement_ir.get("root_id") if preprocessing.requirement_ir else None
        atomic_ids = list(preprocessing.requirement_ir.get("atomic_units", [])) if preprocessing.requirement_ir else []
        requirement_ids = (
            list(preprocessing.requirement_ir.get("node_order", []))
            if preprocessing.requirement_ir
            else []
        )
        states = {
            node_id: ("DISCOVERED" if preprocessing.ok else "FAILED")
            for node_id in requirement_ids
        }

        artifacts.update(artifact_store.write_preprocessing(
            requirement_ir=preprocessing.requirement_ir,
            dependency_graph=preprocessing.dependency_graph,
        ))

        if requirement_ids:
            self._runtime.traceability.store_requirement_ids(requirement_ids)

        for error in preprocessing.errors:
            await self._log("Compiler", error, "error")

        if not preprocessing.ok:
            await self._log("Compiler", "PREPROCESSING pass failed.", "error")
            return CompilationResult(
                ok=False,
                root_id=root_id,
                states=states,
                failed_nodes=requirement_ids,
                artifacts=artifacts,
            )
        if rank <= 1:
            history.commit("1 preprocessing", [".arc"])
        visuals = None
        visual_future = None
        if rank < 4:
            visuals = VisualReferenceResolver().resolve(
                request.requirement_path, preprocessing.requirement_ir,
            )
            if visuals.references:
                visual_path = artifact_store.frontend_design_root / "visual_cache.json"
                visual_future = visual_executor.submit(
                    VisualReferenceAnalyzer.from_env(artifact_store.root).analyze_cached,
                    visuals.references, visual_path,
                    persist=False,
                )
                await self._log("Compiler", "Visual reference analysis started in the background.")
        if rank == 5:
            self._runtime.traceability.merge_database_schema_links(database_traceability(saved["database"]))
            self._runtime.traceability.merge_design_links(design_traceability(saved["design"]))
            self._runtime.traceability.merge_frontend_design_links(frontend_ir_traceability(saved["frontend"]["frontend_ir"]))
            for rid in requirement_ids:
                states[rid] = "FRONTEND_LOWERED"
            failed_gate = None
            if not request.resume:
                failed_gate = await self._build_gate(request, "Restart from lowered", root_id, states, artifacts)
            if request.stop_after_lowering:
                await self._log("Compiler", "Stopped after lowering; design artifacts and generated sources are ready for inspection.")
                return failed_gate or CompilationResult(ok=True, root_id=root_id, states=states, artifacts=artifacts)
            return await self._continue_after_lowering(
                request=request, artifact_store=artifact_store, preprocessing=preprocessing,
                database_schema=saved["database"], design_ir=project_lowering_design(saved["design"]),
                frontend_ir=saved["frontend"]["frontend_ir"], backend_routes=saved["backend_routes"],
                project_manifest=project_manifest,
                root_id=root_id, states=states, artifacts=artifacts,
            )

        # ===================================================================
        #                    Compiler Database Stage
        # ===================================================================

        self._runtime.events.mark_phase_started(
            "DATABASE", "ARC database stage started."
        )

        await self._log("Compiler", "Running database schema design passes." if rank < 2 else "Restoring checkpoint database schema.")
        try:
            model = self._model or Model.from_env()
            if hasattr(model, "set_usage_path"):
                model.set_usage_path(artifact_store.root / "model_usage.jsonl")
        except ModelConfigurationError as exc:
            await self._log("Compiler", str(exc), "error")
            return CompilationResult(
                ok=False,
                root_id=root_id,
                states=states,
                failed_nodes=atomic_ids,
                artifacts=artifacts,
            )
        if rank < 2:
            database_design = DatabaseSchemaPass(model, artifact_store.root)
            database = database_design.compile(
                preprocessing.requirement_ir,
                preprocessing.dependency_graph,
            )
            states.update(database.node_states)
            links = database_traceability(database.schema)
            for error in database.errors:
                await self._log("Compiler", error, "warning")
            for warning in database.warnings:
                await self._log("Compiler", warning, "warning")
            if not database.ok:
                failed_nodes = sorted(node_id for node_id, state in states.items() if state == "FAILED")
                await self._log(
                    "Compiler",
                    "DATABASE_SCHEMA design issues recorded; continuing with the committed/partial schema.",
                    "warning",
                )
            artifacts["database_schema"] = artifact_store.write_database_schema(database.schema, er_model=database.er_model)
            artifacts["database_er_model"] = str(artifact_store.database_root / "er_model.json")
            artifacts["database_schema_ir"] = artifacts["database_schema"]
            self._runtime.traceability.merge_database_schema_links(links)
            history.commit("2.1 database schema design", [".arc"])
            stage_checkpoint(request.output_dir, "database")

        else:
            database = SimpleNamespace(schema=saved["database"])
            self._runtime.traceability.merge_database_schema_links(database_traceability(database.schema))

        if rank < 2:
            checkpoints.save("database", preprocessing={
                "requirement_ir": preprocessing.requirement_ir,
                "dependency_graph": preprocessing.dependency_graph,
            }, database=database.schema)

        # ===================================================================
        #                 Database Lowering: Static SQLite Sources
        # ===================================================================

        if rank < 3:
            lowered_database = lower_database(
                request.output_dir, artifact_store, project_manifest,
            )
            artifacts.update(lowered_database.artifacts)
            for warning in lowered_database.warnings:
                await self._log("Compiler", warning, "warning")
            for error in lowered_database.errors:
                await self._log("Compiler", error, "warning")
            if not lowered_database.ok:
                await self._log(
                    "Compiler",
                    "Database lowering failed; stopping before backend design.",
                    "warning",
                )
                return CompilationResult(
                    ok=False, root_id=root_id, states=states, artifacts=artifacts,
                )
            history.commit("2.2 database schema lowering", [
                ".arc", "backend/src/db", "backend/sql", "shared/src",
            ])
            stage_checkpoint(request.output_dir, "database-lowering")
            failed_gate = await self._build_gate(request, "2.2 database schema lowering", root_id, states, artifacts)

        else:
            lowered_database = SimpleNamespace(manifest=saved["database_manifest"])

        # ===================================================================
        #                    Compiler Design Stage
        # ===================================================================

        self._runtime.events.mark_phase_started(
            "DESIGN", "ARC backend design stage started."
        )

        if rank < 3:
            backend_design = BackendDesignPass(model, artifact_store.root)
            await self._log(
                "Compiler",
                "Designing in depth-first postorder: leaf analysis/architecture, then one parent integration using existing APIs only.",
            )
            design = backend_design.compile(
                preprocessing.requirement_ir,
                preprocessing.dependency_graph,
                database.schema,
            )
            states.update(design.node_states)
            for error in design.errors:
                await self._log("Compiler", error, "warning")
            for warning in design.warnings:
                await self._log("Compiler", warning, "warning")
            artifacts.update(artifact_store.write_design(design_ir=design.design_ir))
            if not design.ok:
                failed_nodes = sorted(node_id for node_id, state in states.items() if state == "FAILED")
                if not failed_nodes:
                    failed_nodes = list(preprocessing.requirement_ir.get("atomic_units", []))
                await self._log(
                    "Compiler",
                    "Backend blueprint validation failed; stopping before backend lowering.",
                    "error",
                )
                return CompilationResult(ok=False, root_id=root_id, states=states,
                                         failed_nodes=failed_nodes, artifacts=artifacts)

            self._runtime.traceability.merge_design_links(design_traceability(design.design_ir))
            history.commit("3.1 backend design", [".arc"])
            stage_checkpoint(request.output_dir, "backend-design")

        else:
            design = SimpleNamespace(design_ir=saved["design"])
            self._runtime.traceability.merge_design_links(design_traceability(design.design_ir))

        if rank < 3:
            checkpoints.save("backend-ir", database_manifest=lowered_database.manifest, design=design.design_ir)

        # Keep canonical blueprints in design artifacts/checkpoints. Existing
        # consumers receive a deterministic view, never a second model design.
        backend_blueprint = design.design_ir
        design = SimpleNamespace(design_ir=project_lowering_design(backend_blueprint))

        if rank < 4:
            self._runtime.events.mark_phase_started("SKELETON", "Generating backend framework from Backend IR.")
            await self._log("Compiler", "Planning backend files, separate input/context/output contracts, module dependencies, and HTTP adapters.")
            lowered_backend = lower_backend(
                request.output_dir, artifact_store, project_manifest,
                blueprint=backend_blueprint, schema=database.schema,
                database_manifest=lowered_database.manifest, default_port=request.web_port,
            )
            artifacts.update(lowered_backend.artifacts)
            for warning in lowered_backend.warnings:
                await self._log("Compiler", warning, "warning")
            for error in lowered_backend.errors:
                await self._log("Compiler", error, "error")
            if not lowered_backend.ok:
                return CompilationResult(ok=False, root_id=root_id, states=states, artifacts=artifacts)
            backend_glue = SimpleNamespace(route_registry=lowered_backend.route_registry)
            await self._log("Compiler", "Backend framework generated; business bodies remain for implementation.")
            history.commit("3.2 backend lowering", [".arc/lowering/backend", "backend/src", "shared/src"])
            stage_checkpoint(request.output_dir, "backend-lowering")
            failed_gate = await self._build_gate(request, "3.2 backend lowering", root_id, states, artifacts)
            if failed_gate is not None:
                return failed_gate
        else:
            backend_glue = SimpleNamespace(route_registry=saved["backend_routes"])

        # ===================================================================
        #                 Compiler Frontend Design Stage
        # ===================================================================

        self._runtime.events.mark_phase_started(
            "FRONTEND", "ARC frontend design stage started."
        )

        if rank < 4:
            assert visuals is not None
            visual_path = artifact_store.frontend_design_root / "visual_cache.json"
            if visual_future is not None:
                await self._log("Compiler", "Waiting for visual reference analysis before frontend IR generation.")
                try:
                    visual_analysis = await asyncio.wrap_future(visual_future)
                    VisualReferenceAnalyzer.persist_cached(visual_analysis.references, visual_path)
                except Exception as exc:
                    await self._log(
                        "Compiler",
                        f"ARC4115 VISUAL_ANALYSIS_FAILED: {type(exc).__name__}: {exc}; continuing without image analysis.",
                        "warning",
                    )
                    visual_analysis = VisualReferenceAnalysisResult(
                        references=[], errors=[],
                    )
            else:
                visual_analysis = VisualReferenceAnalysisResult()
            if visual_path.is_file():
                artifacts["frontend_visual_references"] = str(visual_path)
            await self._log(
                "Compiler",
                "Generating frontend IR in depth-first postorder: four local passes per node, including parent composition; no review or validation.",
            )
            frontend = FrontendIRGenerationPass(model, artifact_store.root).compile(
                preprocessing.requirement_ir,
                preprocessing.dependency_graph,
                backend_blueprint,
                visuals.references,
                visual_analysis.references,
            )
            for issue in [*visuals.errors, *visual_analysis.errors]:
                frontend.report["warnings"].append(issue.format())
            if frontend.report["warnings"] and frontend.report["status"] == "GENERATED":
                frontend.report["status"] = "GENERATED_WITH_WARNINGS"
            # Preserve partial design work for subsequent lowering and implementation.
            artifacts.update(artifact_store.write_frontend_design(
                frontend_ir=frontend.frontend_ir,
                report=frontend.report,
                batches=frontend.batches,
                traceability=frontend.traceability,
            ))
            self._runtime.traceability.merge_frontend_design_links(
                frontend_ir_traceability(frontend.frontend_ir)
            )
            failed_requirements = {
                rid for task in frontend.report["failed_tasks"] for rid in task["requirement_ids"]
            }
            pending_requirements = {rid for gap in frontend.report.get("pending_capabilities", [])
                                    for rid in gap["requirement_ids"]}
            for rid in requirement_ids:
                states[rid] = "FRONTEND_IR_GENERATED_WITH_WARNINGS" if rid in failed_requirements | pending_requirements else "FRONTEND_IR_GENERATED"
            for warning in frontend.report["warnings"]:
                await self._log("Compiler", warning, "warning")
            for failure in frontend.report["failed_tasks"]:
                await self._log("Compiler", f"{failure['phase']}: {failure['message']}", "warning")
            history.commit("4.1 frontend IR generation", [".arc"])
            stage_checkpoint(request.output_dir, "frontend-design")
        else:
            frontend = SimpleNamespace(frontend_ir=saved["frontend"]["frontend_ir"],
                                       report=saved["frontend"]["report"], ok=True)
            failed_requirements = set()
            self._runtime.traceability.merge_frontend_design_links(frontend_ir_traceability(frontend.frontend_ir))
            for rid in requirement_ids:
                states[rid] = ("FRONTEND_IR_GENERATED_WITH_WARNINGS"
                               if any(rid in gap["requirement_ids"] for gap in frontend.frontend_ir.get("pending_capabilities", []))
                               else "FRONTEND_IR_GENERATED")

        if not frontend.ok:
            await self._log(
                "Compiler",
                "Frontend design produced incomplete requirement input; continuing with partial IR and lowering placeholders.",
                "warning",
            )
        if rank < 4:
            checkpoints.save("frontend-ir", backend_routes=backend_glue.route_registry,
                             frontend={"frontend_ir": frontend.frontend_ir,
                                       "report": frontend.report})
        self._runtime.events.mark_phase_started("FRONTEND_LOWERING", "Lowering frontend IR to React.")
        await self._log("Compiler", "Deterministically lowering React components, types, wiring and behavior placeholders; no model calls.")
        lowered_frontend = FrontendReactLowerer(request.output_dir).lower(
            frontend.frontend_ir, design.design_ir, backend_glue.route_registry, request.web_port,
        )
        artifacts.update(artifact_store.write_frontend_lowering(
            report=lowered_frontend.report, sources=lowered_frontend.sources, batches=lowered_frontend.batches,
        ))
        for error in lowered_frontend.errors:
            await self._log("Compiler", error, "error")
        for warning in lowered_frontend.warnings:
            await self._log("Compiler", warning, "warning")
        if not lowered_frontend.ok:
            for rid in requirement_ids:
                states[rid] = "FRONTEND_LOWERING_FAILED"
            return CompilationResult(
                ok=False, root_id=root_id, states=states, artifacts=artifacts,
            )
        try:
            artifacts.update(artifact_store.write_frontend_sources(lowered_frontend.sources))
        except (OSError, ValueError) as exc:
            await self._log("Compiler", f"Cannot publish frontend skeleton: {exc}", "error")
            return CompilationResult(ok=False, root_id=root_id, states=states, artifacts=artifacts)
        for rid in requirement_ids:
            states[rid] = "FRONTEND_LOWERED"
        history.commit("4.2 frontend React lowering", [".arc", "frontend"])
        failed_gate = await self._build_gate(request, "Frontend lowering", root_id, states, artifacts)
        artifacts.update(artifact_store.write_frontend_lowering(
            report={**lowered_frontend.report, "build_status": "FAILED" if failed_gate is not None else "PASSED"},
            sources=lowered_frontend.sources, batches=lowered_frontend.batches,
        ))
        if failed_gate is not None:
            for rid in requirement_ids:
                states[rid] = "FRONTEND_BUILD_FAILED"
            await self._log(
                "Compiler",
                "Frontend build/typecheck failed; stopping for inspection."
                if request.stop_after_lowering else "Frontend build/typecheck failed; continuing to TDD for repair.",
                "warning",
            )
        history.commit("4.3 frontend build accepted", [".arc/lowering/frontend"])
        stage_checkpoint(request.output_dir, "lowered")
        checkpoints.save("lowered")
        await self._log(
            "Compiler",
            "Frontend React lowering completed; build and typecheck passed."
            if failed_gate is None
            else ("Frontend React lowering completed; build/typecheck failed."
                  if request.stop_after_lowering else "Frontend React lowering completed; build/typecheck feedback deferred to TDD."),
            "success" if failed_gate is None else "warning",
        )
        if request.stop_after_lowering:
            await self._log("Compiler", "Stopped after lowering; design artifacts and generated sources are ready for inspection.")
            return failed_gate or CompilationResult(
                ok=frontend.ok, root_id=root_id, states=states,
                failed_nodes=sorted(failed_requirements), artifacts=artifacts,
            )
        return await self._continue_after_lowering(
            request=request, artifact_store=artifact_store, preprocessing=preprocessing,
            database_schema=database.schema, design_ir=design.design_ir,
            frontend_ir=frontend.frontend_ir, backend_routes=backend_glue.route_registry,
            project_manifest=project_manifest,
            root_id=root_id, states=states, artifacts=artifacts, model=model,
        )

    async def _continue_after_lowering(
        self, *, request, artifact_store, preprocessing, database_schema, design_ir,
        frontend_ir, backend_routes, project_manifest, root_id, states, artifacts,
        model=None,
    ) -> CompilationResult:
        """Restore deterministic binding metadata without regenerating installed source files."""
        await self._log("Compiler", "Preparing code bindings; continuing to test generation and TDD.")
        try:
            model = model or self._model or Model.from_env()
            if hasattr(model, "set_usage_path"):
                model.set_usage_path(artifact_store.root / "model_usage.jsonl")
            def checked(result):
                if not result.ok:
                    raise ValueError("; ".join(result.errors))
                return result
            backend_tables = artifact_store.backend_lowering_root
            if not (backend_tables / 'manifest.json').is_file():
                raise ValueError("Missing backend lowering registries; run the current backend lowering stage first.")
            read_table = lambda name: json.loads((backend_tables / f'{name}.json').read_text())
            symbols = SimpleNamespace(registry=read_table('symbols'))
            types = SimpleNamespace(manifest=read_table('types'))
            modules = {kind: read_table('modules_' + {'DB': 'db', 'FUNC': 'func', 'API': 'api'}[kind])
                       for kind in ('DB', 'FUNC', 'API')}
            report_path = artifact_store.frontend_lowering_root / "lowering.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            bindings = checked(CodeBindingLowerer().lower(
                output_root=request.output_dir, requirement_ir=preprocessing.requirement_ir,
                dependency_graph=preprocessing.dependency_graph, design_ir=design_ir, frontend_ir=frontend_ir,
                backend_symbol_registry=symbols.registry, backend_type_manifest=types.manifest,
                backend_module_manifests=modules, backend_route_registry=backend_routes,
                frontend_lowering_report=report,
            ))
        except (ModelConfigurationError, OSError, ValueError, KeyError) as exc:
            await self._log("Compiler", f"TDD handoff failed: {exc}", "error")
            return CompilationResult(ok=False, root_id=root_id, states=states, artifacts=artifacts)
        artifacts["code_bindings"] = artifact_store.write_code_bindings(bindings.registry)
        ProjectGitHistory(request.output_dir).commit("4.4 code bindings", [".arc/lowering/code_bindings.json"])
        return await self._run_tdd(
            request=request, artifact_store=artifact_store, requirement_ir=preprocessing.requirement_ir,
            dependency_graph=preprocessing.dependency_graph, database_schema=database_schema,
            design_ir=design_ir, frontend_ir=frontend_ir, code_binding_registry=bindings.registry,
            model=model, root_id=root_id, states=states, artifacts=artifacts,
        )

    async def _build_gate(
        self,
        request: CompilationRequest,
        stage: str,
        root_id: str | None,
        states: dict[str, str],
        artifacts: dict[str, str],
    ) -> CompilationResult | None:
        build = ProjectBuilder(request.output_dir).build()
        if not build.ok:
            for output in build.errors:
                await self._log("Compiler", f"{stage}: {output}", "warning")
            return CompilationResult(
                ok=False, root_id=root_id, states=states, artifacts=artifacts,
            )
        # The build already type-checks shared, frontend and backend; tests are
        # the only workspace omitted from the root build script.
        typecheck = TestRunner(request.output_dir).run_workspace_typecheck("tests")
        if typecheck.status != "PASSED":
            for output in [typecheck.stdout, typecheck.stderr, typecheck.error or ""]:
                if output:
                    await self._log("Compiler", f"{stage}: {output}", "warning")
            return CompilationResult(
                ok=False, root_id=root_id, states=states, artifacts=artifacts,
            )
        await self._log("Compiler", f"{stage}: build and tests typecheck passed.")
        return None

    async def _run_tdd(
        self,
        *,
        request: CompilationRequest,
        artifact_store: CompilerArtifactStore,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
        database_schema: dict[str, Any],
        design_ir: dict[str, Any],
        frontend_ir: dict[str, Any],
        code_binding_registry: dict[str, Any],
        model: StructuredModel,
        root_id: str | None,
        states: dict[str, str],
        artifacts: dict[str, str],
    ) -> CompilationResult:
        # ===================================================================
        #               Stage 5: Test Generation
        # ===================================================================

        self._runtime.events.mark_phase_started(
            "TEST_GENERATION", "ARC test generation stage started."
        )

        await self._log(
            "Compiler",
            "Preparing the compiler-owned Vitest, Supertest, and Playwright test environment.",
        )
        test_environment = TestEnvironmentInitializer(
            request.output_dir,
            backend_port=request.web_port,
        ).initialize()
        for error in test_environment.errors:
            await self._log("Compiler", error, "error")
        if not test_environment.ok:
            await self._log("Compiler", "TEST_ENVIRONMENT initialization failed.", "error")
            return CompilationResult(
                ok=False,
                root_id=root_id,
                states=states,
                artifacts=artifacts,
            )
        await self._log(
            "Compiler",
            "TEST_ENVIRONMENT_READY: test workspace and browser prerequisites are available.",
        )

        atomic_ids = {
            str(value) for value in requirement_ir.get("atomic_units", []) if str(value)
        }
        folder_ids = {
            str(value) for value in requirement_ir.get("folder_nodes", []) if str(value)
        }
        try:
            order = _tdd_postorder(requirement_ir)
        except ValueError as exc:
            await self._log("Compiler", f"ARC4548 TDD_ORDER_INVALID: {exc}", "error")
            return CompilationResult(
                ok=False, root_id=root_id, states=states,
                artifacts=artifacts,
            )
        await self._log("Compiler", f"TDD post-order DFS (dependency waves ignored): {order}")

        history = ProjectGitHistory(request.output_dir)
        test_generation = RequirementTestGenerationPass(
            model, request.output_dir, artifact_store,
        )
        try:
            existing_manifest = load_tdd_manifest(request.output_dir) if request.resume else None
            progress = TDDProgress(request.output_dir, requirement_ir, order, resume=request.resume)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            await self._log("Compiler", f"Cannot restore TDD progress: {exc}", "error")
            return CompilationResult(ok=False, root_id=root_id, states=states, artifacts=artifacts)
        artifacts["tdd_progress"] = str(progress.path)
        if existing_manifest is not None:
            artifacts["test_manifest"] = str(artifact_store.tests_root / "test_manifest.json")
            self._runtime.traceability.merge_test_links(existing_manifest)
        previous_nodes = dict(progress.nodes) if request.resume else {}
        if request.resume:
            if existing_manifest is None and any(row["status"] == "TESTS_PASSED" for row in previous_nodes.values()):
                await self._log("Compiler", "Cannot resume: completed TDD nodes have no saved test manifest.", "error")
                return CompilationResult(ok=False, root_id=root_id, states=states, artifacts=artifacts)
            await self._log("Compiler", f"TDD resume: {len(previous_nodes)} already-started nodes retained; "
                            f"{len(order) - len(previous_nodes)} unstarted nodes remain.")
        orchestrator = NodeTDDOrchestrator(
            model,
            request.output_dir,
            requirement_ir=requirement_ir,
            code_binding_registry=code_binding_registry,
            frontend_ir=frontend_ir,
            test_manifest=existing_manifest,
        )
        self._runtime.events.mark_phase_started(
            "TDD", "ARC node-by-node test-driven implementation started.",
        )
        failed_requirements: set[str] = set()
        for rid, row in previous_nodes.items():
            states[rid] = row["status"]
            if row["status"] in {"STARTED", "FAILED"}:
                failed_requirements.add(rid)

        async def warn_node_failure(requirement_id, stage, result):
            failed = set(result.failed_requirements) if hasattr(result, "failed_requirements") else set()
            failed.add(requirement_id)
            failed_requirements.update(failed)
            for failed_id in failed:
                states[failed_id] = "FAILED"
                if failed_id in progress.data["order"]:
                    progress.mark(failed_id, "FAILED", stage)
            await self._log(
                "Compiler",
                f"{requirement_id}: {stage} failed; continuing with remaining stages where possible. "
                "Accepted changes are retained; rejected patches remain rolled back.",
                "warning",
            )

        for requirement_id in order:
            if requirement_id in previous_nodes:
                await self._log("Compiler", f"Resume skips already-started requirement {requirement_id}: "
                                f"{previous_nodes[requirement_id]['status']}")
                continue
            progress.mark(requirement_id, "STARTED", "test generation")
            if requirement_id in folder_ids:
                targets = CodeTargetResolver(code_binding_registry).resolve_requirement_targets(
                    requirement_id,
                )
                has_screen = any(
                    isinstance(screen, dict)
                    and requirement_id in screen.get("requirement_ids", [])
                    for screen in frontend_ir.get("ui", [])
                )
                if not targets["owned_targets"] and not has_screen:
                    states[requirement_id] = "AGGREGATE_NO_UI"
                    progress.mark(requirement_id, "AGGREGATE_NO_UI", "no owned UI")
                    await self._log(
                        "Compiler",
                        f"{requirement_id} has no owned targets or associated UI screen; "
                        "its child requirements are verified independently.",
                        "warning",
                    )
                    continue
            await self._log("Compiler", f"Generating and freezing tests for {requirement_id}.")
            generated_tests = test_generation.generate_requirement(
                requirement_id=requirement_id,
                requirement_ir=requirement_ir,
                database_schema=database_schema,
                design_ir=design_ir,
                frontend_ir=frontend_ir,
                code_binding_registry=code_binding_registry,
                environment_manifest=test_environment.manifest,
                existing_manifest=orchestrator.test_manifest,
            )
            artifacts.update(generated_tests.artifacts)
            states.update(generated_tests.node_states)
            for error in generated_tests.errors:
                await self._log("Compiler", error, "warning")
            if not generated_tests.ok:
                await warn_node_failure(requirement_id, "test generation", generated_tests)
                continue
            orchestrator.test_manifest = generated_tests.manifest
            self._runtime.traceability.merge_test_links(generated_tests.manifest)
            history.commit(f"5 test generation {requirement_id}", [".arc", "tests"])

            if requirement_id in atomic_ids:
                backend_implementation = orchestrator.implement_backend([requirement_id])
                for error in backend_implementation.errors:
                    await self._log("NodeTDDOrchestrator", error, "warning")
                if not backend_implementation.ok:
                    await warn_node_failure(requirement_id, "backend implementation", backend_implementation)
                else:
                    states[requirement_id] = "BACKEND_IMPLEMENTED"
                if backend_implementation.changed_files:
                    history.commit(
                        f"6.1 backend implementation {requirement_id}",
                        backend_implementation.changed_files,
                    )

            frontend_implementation = orchestrator.implement_frontend([requirement_id])
            for error in frontend_implementation.errors:
                await self._log("NodeTDDOrchestrator", error, "warning")
            if not frontend_implementation.ok:
                await warn_node_failure(requirement_id, "frontend implementation", frontend_implementation)
            else:
                states[requirement_id] = "FRONTEND_IMPLEMENTED"
            if frontend_implementation.changed_files:
                history.commit(
                    f"6.2 frontend implementation {requirement_id}",
                    frontend_implementation.changed_files,
                )

            # Both initial implementations precede any behavioral test execution.
            tests = orchestrator.run_test_layers([requirement_id])
            for error in tests.errors:
                await self._log("NodeTDDOrchestrator", error, "warning")
            if not tests.ok:
                await warn_node_failure(requirement_id, "test-driven repair", tests)
                continue
            failed_requirements.discard(requirement_id)
            states[requirement_id] = "TESTS_PASSED"
            progress.mark(requirement_id, "TESTS_PASSED", "test layers")
            await self._log("Compiler", f"TDD completed for {requirement_id}.")
            if "." not in requirement_id:
                stage_checkpoint(request.output_dir, requirement_id)

        failed_nodes = sorted(failed_requirements)
        for failed_id in failed_nodes:
            states[failed_id] = "FAILED"
        if failed_nodes:
            await self._log(
                "Compiler",
                "TDD traversal finished with failed requirements: " + ", ".join(failed_nodes),
                "warning",
            )
        final_validation_errors = orchestrator.validate_final()
        if final_validation_errors:
            for error in final_validation_errors:
                await self._log("Compiler", f"Final validation: {error}", "error")
            return CompilationResult(
                ok=False, root_id=root_id, states=states,
                failed_nodes=failed_nodes, artifacts=artifacts,
            )
        return CompilationResult(
            ok=not failed_nodes, root_id=root_id, states=states,
            failed_nodes=failed_nodes, artifacts=artifacts,
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
