"""Execute the current design, lowering, implementation and validation stages."""
from __future__ import annotations

import inspect
from functools import partial
from contextvars import copy_context
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .contracts import CompilationRequest, CompilationResult
from .budgets import ExecutionBudgets
from .run_report import CompilationRunReport
from .preprocessing.queries import leaf_ids
from .foundation import InitializationInput, PreprocessingStage, ProjectInitializationStage
from .runtime import CompilationState, CompilerDependencies
from .stages import StageRunner
from utils.jsonio import read_json, write_json_atomic
from utils.logging import SynchronousLog


class _BusinessPipeline:
    def __init__(self, request: CompilationRequest, state: CompilationState,
                 dependencies: CompilerDependencies) -> None:
        self.request = request
        self.state = state
        self.log_cb = dependencies.log_cb
        self.model = dependencies.model
        self.runner = StageRunner(state.stages)

    async def log(self, message, status=None):
        if self.log_cb:
            result = self.log_cb('Compiler', message, status, None)
            if inspect.isawaitable(result):
                await result
        else:
            self.logger.write(message, status=status, node_id=None)

    async def run(self) -> CompilationResult:
        # Stage: Preprocessing - validate requirements and workspace ===========================
        request = self.request
        preprocessing = await PreprocessingStage(request, runner=self.runner).run()
        parsed = preprocessing.value
        output = parsed.output_root
        root = output / '.arc'
        self.logger = SynchronousLog('Compiler', workspace_root=output)
        requirements = parsed.requirement_ir
        leaves = leaf_ids(requirements)
        states = self.state.states
        states.update(parsed.states)
        artifacts = self.state.artifacts
        artifacts.update(parsed.artifacts)
        run_id = self.state.run_id
        summary = root / 'workflow/report.json'
        artifacts['workflow'] = str(summary)
        run = CompilationRunReport(summary, run_id, states, artifacts)
        self.state.report = run

        # Process: Record stage progress and recoverable failures ==============================
        async def begin(name):
            record = run.begin(name)
            await self.log(name + ': RUNNING')
            return record

        async def finish(record, path=None, warnings=()):
            run.complete(record, path, warnings)
            await self.log(record['stage'] + ': COMPLETED')
            for warning in warnings:
                await self.log(warning, 'warning')

        async def stage_failed(record, exc):
            message = f'{record["stage"]}: {exc}'
            run.fail(record, message)
            await self.log(message + '; continuing subsequent stages.', 'warning')

        async def run_guarded_stage(record, action, fallback=None):
            # Keep actions lazy so setup failures belong to the explicit stage record.
            try:
                return action()
            except Exception as exc:
                await stage_failed(record, exc)
                return fallback

        async def complete_design(record, result, path):
            artifacts[record['stage']] = str(path)
            if result.ok:
                await finish(record, path, result.warnings)
            else:
                await stage_failed(record, '; '.join(result.errors))

        visual_executor = None
        visual_future = None
        visual_report = None
        try:
            from model.client import Model
            from .database_design import DatabaseSchemaPass
            from .backend_design import BackendDesignPass
            from .frontend_design import FrontendDesignPass
            from .database_lowering import DatabaseLoweringPass
            from .backend_lowering import BackendLoweringPass
            from .frontend_lowering import FrontendLoweringPass
            from .lowering.compilation import check_compilation
            from .implementation import NodeTDDOrchestrator
            from .build.stage import ProjectBuilder
            from .validation_preparation.stage import ValidationPreparationPass, check_validation_readiness
            from .stage_repair import repair_gate

            from utils.environment import ensure_dotenv_loaded
            ensure_dotenv_loaded()
            self.state.budgets = ExecutionBudgets.from_environment()
            repair_gate = partial(repair_gate, budgets=self.state.budgets)
            # Stage: Project initialization ====================================================
            initialization_report = await begin('project_initialization')

            try:
                initialized = await ProjectInitializationStage(
                    InitializationInput(output, request.web_port), runner=self.runner).run()
            except Exception as exc:
                await stage_failed(initialization_report, exc)
            else:
                await finish(initialization_report, initialized.value.report_path)

            # Stage: Visual analysis - launch alongside database design ========================
            model = self.model if self.model is not None else Model.from_env()
            frontend_designer = FrontendDesignPass(
                model, root, reference_root=Path(request.requirement_path).resolve().parent)
            # Retain the explicit report while database stages run in parallel.
            visual_report = await begin('visual_analysis')

            def analyze_images():
                seen = set()
                for rid in leaves:
                    for page in requirements['nodes'][rid].get('pages', []):
                        reference = (page.get('reference') or '').replace('\\', '/')
                        if reference and reference not in seen:
                            seen.add(reference)
                            try:
                                frontend_designer.analyze(page['name'], reference, rid)
                            except Exception as exc:
                                frontend_designer.visual_failures[reference] = str(exc)
                                frontend_designer.visual_log.warning(f'Skipping {reference}: {exc}', node_id=rid)

            # Separate thread: synchronous database design must not block image analysis.
            # One writer owns the visual cache; duplicate paths are analyzed once.
            visual_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='arc-visual')
            visual_future = visual_executor.submit(copy_context().run, analyze_images)
            await self.log('Visual analysis is running in the background alongside database design.')

            # Stage: Database design ===========================================================
            database_report = await begin('database_design')
            from .database_design.state import DatabaseSchemaState
            from .database_design.refinement import refine_er_model
            empty_er_model = DatabaseSchemaState().to_er_model()

            def design_database():
                return DatabaseSchemaPass(model, root).compile(requirements, parsed.dependency_graph)

            # Prepare the fallback before entering the guarded design action.
            database = await run_guarded_stage(
                database_report, design_database,
                SimpleNamespace(schema=refine_er_model(empty_er_model, requirement_ids=set(leaves)).schema,
                    er_model=empty_er_model, node_states={rid: 'FAILED' for rid in leaves},
                    ok=False, errors=['Database design unavailable'], warnings=[]))

            def write_database_artifacts():
                write_json_atomic(root / 'design/database/schema.json', database.schema)
                write_json_atomic(root / 'design/database/er_model.json', database.er_model)
                write_json_atomic(root / 'design/database/report.json', {
                    'status': database.schema['status'],
                    'node_states': database.node_states,
                    'errors': database.errors,
                    'warnings': database.warnings,
                })

            await run_guarded_stage(database_report, write_database_artifacts)
            if database_report['status'] == 'RUNNING':
                if database.schema['status'] != 'RESOLVED' and database.ok:
                    await stage_failed(database_report, 'Database design did not resolve its schema')
                else:
                    await complete_design(database_report, database, root / 'design/database/schema.json')

            from .lowering.recovery import isolate_design, bind_recovery, minimal_lowering
            recovery_reasons = {name: {} for name in ('database', 'backend', 'frontend')}

            # Process: Lower a design with repair and isolation fallbacks ======================
            async def lower_design(name, lowerer, design):
                lowering_report = await begin(name + '_lowering')
                plan = None
                effective_design = design

                def render_lowering():
                    if name == 'database':
                        return lowerer.compile(effective_design, output)
                    return lowerer.compile(requirements, effective_design, output)

                def check_lowering():
                    try:
                        compilation = check_compilation(output, name)
                        return {'ok': compilation['status'] == 'COMPILED', 'compilation': compilation}
                    except Exception as exc:
                        return {'ok': False, 'errors': [str(exc)]}
                try:
                    plan = render_lowering()
                except Exception as exc:
                    await self.log(f'{name}: isolating unlowerable design: {exc}', 'warning')
                    try:
                        plan, effective_design = isolate_design(name, lowerer, requirements, effective_design, output)
                        recovery_reasons[name].update(plan.pop('recovery_reasons', {}))
                    except Exception as isolation_error:
                        recovery_reasons[name].update({rid: str(isolation_error)[:1000] for rid in leaves})
                        plan, effective_design = minimal_lowering(name, lowerer, requirements, output)
                result = check_lowering()
                if not result['ok']:
                    try:
                        result = repair_gate(
                            model, output, name + '_lowering', check_lowering, result,
                            contracts=effective_design)
                    except Exception as exc:
                        await self.log(f'{name}: lowering repair unavailable; using recovery scaffolding: {exc}', 'warning')
                if not result['ok']:
                    # The populated source plan still cannot compile. Replace only
                    # stage-owned scaffolding, preserving user-modified files.
                    recovery_reasons[name].update({rid: str(result)[:1000] for rid in leaves})
                    plan, effective_design = minimal_lowering(name, lowerer, requirements, output)
                    result = check_lowering()
                if result['ok']:
                    manifest_path = root / 'lowering' / name / 'manifest.json'
                    manifest = read_json(manifest_path, {})
                    from .lowering.files import digest
                    for relative in manifest.get('files', {}):
                        manifest['files'][relative] = digest((output / relative).read_text(encoding='utf-8'))
                    write_json_atomic(manifest_path, manifest)
                    await finish(lowering_report, root / 'lowering' / name / 'plan.json')
                else:
                    await stage_failed(lowering_report, name + ': minimal framework still needs repair: ' + str(result))
                if recovery_reasons[name]:
                    filename = 'schema.json' if name == 'database' else name + '_ir.json'
                    write_json_atomic(root / 'design' / name / filename, effective_design)
                return plan, effective_design, lowering_report

            # Stage: Database lowering =========================================================
            database_plan, database.schema, database_lowering_report = await lower_design(
                'database', DatabaseLoweringPass(), database.schema)
            # Backend design reads the visual cache, so wait at this boundary.
            # Process: Join visual analysis before backend design ==============================
            await self.log('Waiting for visual evidence before backend design.')
            try:
                visual_future.result()
            except Exception as exc:
                frontend_designer.visual_failures['background_analysis'] = str(exc)
                frontend_designer.visual_log.warning('Background visual analysis skipped: ' + str(exc))
            run.complete(visual_report, root / 'visual_cache.json',
                status='COMPLETED_WITH_WARNINGS' if frontend_designer.visual_failures else 'COMPLETED',
                warnings=[f'{path}: {message}' for path, message in frontend_designer.visual_failures.items()])
            await self.log('visual_analysis: COMPLETED')
            # Stage: Backend design ============================================================
            backend_report = await begin('backend_design')

            def design_backend():
                return BackendDesignPass(model, root).compile(requirements, database.er_model)

            backend = await run_guarded_stage(backend_report, design_backend,
                SimpleNamespace(ir={'ir_kind': 'backend_design', 'types': [], 'modules': []},
                    node_states={rid: 'SKIPPED' for rid in leaves}, ok=False,
                    errors=['Backend design unavailable'], warnings=[]))
            if backend_report['status'] == 'RUNNING':
                await complete_design(backend_report, backend, root / 'design/backend/backend_ir.json')
            # Stage: Backend lowering ==========================================================
            backend_plan, backend.ir, backend_lowering_report = await lower_design(
                'backend', BackendLoweringPass(), backend.ir)
            write_json_atomic(root / 'design/backend/backend_ir.json', backend.ir)

            # Stage: Frontend design ===========================================================
            frontend_report = await begin('frontend_design')
            from .frontend_design.delta import make_ui

            def design_frontend():
                return frontend_designer.compile(requirements, database.er_model, backend.ir)

            frontend = await run_guarded_stage(frontend_report, design_frontend,
                SimpleNamespace(ir={'ir_kind': 'frontend_design',
                    'ui': make_ui('UI_APP', 'Application', 'app', 'Application root.'),
                    'types': [], 'data': [], 'events': [], 'handlers': []},
                    node_states={rid: 'SKIPPED' for rid in leaves}, ok=False,
                    errors=['Frontend design unavailable'], warnings=[]))
            if frontend_report['status'] == 'RUNNING':
                await complete_design(frontend_report, frontend, root / 'design/frontend/frontend_ir.json')
            # Stage: Frontend lowering =========================================================
            frontend_plan, frontend.ir, frontend_lowering_report = await lower_design(
                'frontend', FrontendLoweringPass(), frontend.ir)
            write_json_atomic(root / 'design/frontend/frontend_ir.json', frontend.ir)

            # FileContext derives requirement/file associations from the lowering
            # plans directly; no second binding registry is persisted.
            # Process: Bind recovery work to lowered requirement files =========================
            states.update({rid: 'LOWERED' for rid in leaves})

            backend_recovery = dict(recovery_reasons['backend'])
            frontend_recovery = dict(recovery_reasons['frontend'])
            from .frontend_design.validation import ui_index
            ui_nodes, _ = ui_index(frontend.ir['ui'])
            backend_owners = {rid for row in backend.ir['modules'] for rid in row['reqs']}
            ui_owners = {rid for row in ui_nodes.values() for rid in row['reqs']}
            for rid in leaves:
                if backend.node_states.get(rid) in {'SKIPPED', 'FAILED'}:
                    backend_recovery[rid] = 'Backend design unavailable; implement from the requirement.'
                if database.node_states.get(rid) in {'FAILED', 'SKIPPED'} or rid in recovery_reasons['database']:
                    backend_recovery[rid] = 'Database design/lowering incomplete; repair shared initialization as needed.'
                if frontend.node_states.get(rid) in {'SKIPPED', 'FAILED'} and requirements['nodes'][rid].get('pages'):
                    frontend_recovery[rid] = 'Frontend design unavailable; implement the declared pages from the requirement.'
                if requirements['nodes'][rid].get('pages') and rid not in ui_owners:
                    frontend_recovery.setdefault(rid, 'No UI files bound; implement the declared pages from the requirement.')
                if rid not in backend_owners and rid not in ui_owners and rid not in frontend_recovery:
                    backend_recovery.setdefault(rid, 'No source files bound; implement from the requirement.')
                if rid in backend_recovery and requirements['nodes'][rid].get('pages'):
                    frontend_recovery.setdefault(rid, 'Integrate the recovered backend behavior with the declared pages.')
            backend_plan = bind_recovery(output, 'backend', backend_plan, backend_recovery)
            frontend_plan = bind_recovery(output, 'frontend', frontend_plan, frontend_recovery)
            recovered = set(backend_recovery) | set(frontend_recovery)
            for rid in recovered:
                states[rid] = 'LOWERED_WITH_RECOVERY'
            if recovered:
                message = 'Recovery files bound; continuing implementation for: ' + ', '.join(sorted(recovered))
                run.add_warning(frontend_lowering_report, message)
                await self.log(message, 'warning')
            # Stage: Requirement implementation and acceptance =================================
            if not request.stop_after_lowering:
                implementation_report = await begin('implementation')
                try:
                    implementation = NodeTDDOrchestrator(
                        model, output, port=request.web_port, budgets=self.state.budgets).compile(
                        requirements, database.er_model, backend.ir, frontend.ir,
                        backend_plan, frontend_plan, database_plan)
                    states.update(implementation.node_states)
                    run.failed_nodes.extend(rid for rid in leaves if states[rid] not in {'IMPLEMENTED', 'ALREADY_PASSED'})
                    run.failed_nodes[:] = sorted(set(run.failed_nodes))
                    if implementation.ok and not run.failed_nodes:
                        await finish(implementation_report, root / 'implementation/report.json', implementation.warnings)
                    else:
                        raise ValueError('; '.join(implementation.errors) or 'Requirements not implemented: ' + ', '.join(run.failed_nodes))
                except Exception as exc:
                    message = 'implementation: ' + str(exc)
                    run.fail(implementation_report, message, root / 'implementation/report.json')
                    await self.log(message + '; continuing to build repair.', 'warning')

            # Stage: Production build and repair ===============================================
            build_report = await begin('build')

            def check_build():
                try:
                    result = ProjectBuilder(output).build()
                    return {'ok': result.ok, 'errors': result.errors}
                except Exception as exc:
                    return {'ok': False, 'errors': [str(exc)]}
            build = check_build()
            if not build['ok']:
                def repair_build():
                    return repair_gate(model, output, 'build', check_build, build)

                build = await run_guarded_stage(build_report, repair_build, build)
            if build['ok']:
                await finish(build_report, output / 'frontend/dist/index.html')
            else:
                message = 'build: repair budget exhausted: ' + str(build)
                run.fail(build_report, message)
                await self.log(message, 'warning')
            # Stage: Optional validation preparation ===========================================
            if request.prepare_validation and not request.stop_after_lowering and build['ok']:
                validation_report = await begin('validation_preparation')
                def prepare_validation():
                    validation = ValidationPreparationPass(model, output).generate(requirements)
                    check_validation_readiness(output, validation=True)
                    write_json_atomic(root / 'validation_preparation/report.json',
                                      {'status': 'PREPARED', 'artifacts': validation, 'port': request.web_port})
                    return True
                prepared = await run_guarded_stage(validation_report, prepare_validation)
                if prepared:
                    await finish(validation_report, root / 'validation_preparation/report.json')
            elif request.prepare_validation and not build['ok']:
                run.skip('validation_preparation', ['Build unavailable; validation artifacts cannot be prepared.'])
            # Process: Finalize the compilation result =========================================
            # Completion records execution progress, not just requirement success.
            if run.errors:
                final_status = 'COMPLETED_WITH_ERRORS'
            elif request.stop_after_lowering:
                final_status = 'LOWERED'
            else:
                final_status = 'COMPLETED'
            run.save(final_status)
            return CompilationResult(ok=not run.errors, root_id=requirements['root_id'], states=states,
                failed_nodes=run.failed_nodes, artifacts=artifacts, run_id=run_id, completed=True)
        except Exception as exc:
            run.record_fatal_error(exc)
            if not run.failed_nodes:
                run.failed_nodes.extend(rid for rid in leaves if states[rid] not in {'IMPLEMENTED', 'ALREADY_PASSED'})
            run.save('FAILED')
            await self.log('FAILED: ' + run.errors[-1], 'error')
            return CompilationResult(ok=False, root_id=requirements['root_id'], states=states,
                failed_nodes=run.failed_nodes, artifacts=artifacts, run_id=run_id)
        # Process: Settle background analysis and release run resources ========================
        finally:
            if visual_executor is not None:
                visual_executor.shutdown(wait=True, cancel_futures=True)
                if visual_future is not None and visual_report['status'] == 'RUNNING':
                    error = visual_future.exception() if not visual_future.cancelled() else 'Cancelled'
                    visual_report.update(status='FAILED' if error else 'COMPLETED',
                                         errors=[str(error)] if error else [])
                    run.save('COMPLETED_WITH_ERRORS' if run.errors or error else 'COMPLETED')


from .compiler import Compiler
