"""High-level stage ordering, explicit dependencies and conditional execution."""
from __future__ import annotations

import asyncio

from .backend_design.workflow import BackendDesignInput, BackendDesignStage
from .backend_lowering.workflow import BackendLoweringInput, BackendLoweringStage
from .build.workflow import BuildStage
from .contracts import CompilationRequest, CompilationResult
from .database_design.workflow import DatabaseDesignInput, DatabaseDesignStage
from .database_lowering.workflow import DatabaseLoweringInput, DatabaseLoweringStage
from .foundation import InitializationInput, PreprocessingStage, ProjectInitializationStage
from .frontend_design.workflow import FrontendDesignInput, FrontendDesignStage
from .frontend_lowering.workflow import FrontendLoweringInput, FrontendLoweringStage
from .implementation.workflow import (PreparationInput, ImplementationPreparationStage,
                                     ImplementationInput, ImplementationStage)
from .progress import CompilationProgress
from .runtime import CompilationState, CompilerDependencies
from .stages import StageRunner
from .validation_preparation.workflow import ValidationPreparationInput, ValidationPreparationStage
from .visual_analysis import VisualAnalysisInput, VisualAnalysisStage


class CompilerPipeline:
    def __init__(self, request: CompilationRequest, state: CompilationState,
                 dependencies: CompilerDependencies) -> None:
        self.request, self.state, self.dependencies = request, state, dependencies

    async def run(self) -> CompilationResult:
        request, state = self.request, self.state
        runner = StageRunner(state.stages)
        parsed = (await PreprocessingStage(request, runner=runner).run()).require_value()
        progress = CompilationProgress(state, parsed, self.dependencies.log_cb)
        runner.observer = progress
        output = parsed.output_root
        root = output / '.arc'
        requirements = parsed.requirement_ir
        try:
            state.budgets = self.dependencies.load_budgets()
            await ProjectInitializationStage(InitializationInput(output, request.web_port),
                runner=runner, continue_on_error=True).run()
            model = self.dependencies.model_client()
            visual_stage = VisualAnalysisStage(VisualAnalysisInput(requirements, root,
                request.requirement_path.resolve().parent), runner=runner)
            async with visual_stage.background():
                database = (await DatabaseDesignStage(DatabaseDesignInput(
                    requirements, parsed.dependency_graph, root), model, runner=runner).run()).require_value()
                database_lowering = (await DatabaseLoweringStage(DatabaseLoweringInput(
                    requirements, database.schema, output), model, state.budgets, runner=runner).run()).require_value()
                await progress.message('Waiting for visual evidence before backend design.')
                visual = (await visual_stage.join()).require_value()
                backend = (await BackendDesignStage(BackendDesignInput(
                    requirements, database.er_model, visual, root), model, runner=runner).run()).require_value()
                backend_lowering = (await BackendLoweringStage(BackendLoweringInput(
                    requirements, backend.ir, output), model, state.budgets, runner=runner).run()).require_value()
                frontend = (await FrontendDesignStage(FrontendDesignInput(requirements, database.er_model,
                    backend_lowering.effective_design, visual, root), model, runner=runner).run()).require_value()
                frontend_lowering = (await FrontendLoweringStage(FrontendLoweringInput(
                    requirements, frontend.ir, output), model, state.budgets, runner=runner).run()).require_value()
            prepared = (await ImplementationPreparationStage(PreparationInput(requirements, output,
                database, backend, frontend, database_lowering, backend_lowering, frontend_lowering),
                runner=runner).run()).require_value()
            state.states.update(prepared.node_states)
            if not request.stop_after_lowering:
                implementation = (await ImplementationStage(ImplementationInput(prepared, output,
                    request.web_port), model, state.budgets, runner=runner).run()).require_value()
                state.states.update(implementation.node_states)
                progress.report.failed_nodes.extend(implementation.failed_nodes)
            build = (await BuildStage(output, model, state.budgets, runner=runner).run()).require_value()
            if request.prepare_validation and not request.stop_after_lowering and build.ok:
                await ValidationPreparationStage(ValidationPreparationInput(requirements, output,
                    request.web_port), model, runner=runner).run()
            elif request.prepare_validation and not build.ok:
                progress.report.skip('validation_preparation',
                    ['Build unavailable; validation artifacts cannot be prepared.'])
            return progress.finish(stopped=request.stop_after_lowering)
        except asyncio.CancelledError:
            progress.cancel()
            raise
        except Exception as exc:
            result = progress.fail(exc)
            await progress.message('FAILED: ' + progress.report.errors[-1], 'error')
            return result


from .compiler import Compiler
