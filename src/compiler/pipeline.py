"""Execute the current design, lowering, implementation and validation stages."""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .contracts import CompilationRequest, CompilationResult
from .preprocessing import RequirementPreprocessor
from .preprocessing.traversal import children_by_id, postorder
from utils.jsonio import read_json, write_json_atomic
from utils.logging import SynchronousLog, set_run_id


class Compiler:
    def __init__(self, runtime: Any = None, log_cb: Any = None, model: Any = None) -> None:
        self.log_cb = log_cb
        self.model = model

    async def _log(self, message, status=None):
        if self.log_cb:
            result = self.log_cb('Compiler', message, status, None)
            if inspect.isawaitable(result):
                await result
        else:
            self.logger._write(message, status=status, node_id=None)

    async def compile(self, request: CompilationRequest) -> CompilationResult:
        if request.resume or request.start_from != 'zero':
            raise ValueError('Checkpoint resume is not supported by the current IR; use the individual stage scripts.')
        if not 1 <= request.web_port <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        parsed = RequirementPreprocessor().compile(request.requirement_path)
        if not parsed.ok:
            raise ValueError('; '.join(parsed.errors))
        output = request.output_dir.expanduser().resolve()
        root = output / '.arc'
        self.logger = SynchronousLog('Compiler', workspace_root=output)
        requirements = parsed.requirement_ir
        children = children_by_id(requirements)
        leaves = [rid for rid in postorder(requirements) if not children[rid]]
        states = {rid: 'PENDING' if rid in leaves else 'ORGANIZATIONAL' for rid in requirements['nodes']}
        existing = read_json(root / 'preprocessing/requirement_ir.json', {})
        if existing and existing.get('source') != requirements.get('source'):
            raise ValueError('Requirements differ from the existing workspace; use a new output directory.')
        write_json_atomic(root / 'preprocessing/requirement_ir.json', requirements)
        write_json_atomic(root / 'preprocessing/dependency_graph.json', parsed.dependency_graph)
        artifacts = {'requirements': str(root / 'preprocessing/requirement_ir.json')}
        reports, failed, errors = [], [], []
        run_id = uuid.uuid4().hex
        set_run_id(run_id)
        summary = root / 'workflow/report.json'
        artifacts['workflow'] = str(summary)
        stage = 'preprocessing'

        def save(status):
            write_json_atomic(summary, {'status': status, 'run_id': run_id, 'stage': stage,
                'stages': reports, 'node_states': states, 'errors': errors, 'failed_nodes': failed})

        async def begin(name):
            nonlocal stage
            stage = name
            reports.append({'stage': name, 'status': 'RUNNING'})
            save('RUNNING')
            await self._log(name + ': RUNNING')

        async def finish(path=None, warnings=()):
            reports[-1].update(status='COMPLETED', warnings=list(warnings))
            if path:
                artifacts[stage] = str(path)
            save('RUNNING')
            await self._log(stage + ': COMPLETED')
            for warning in warnings:
                await self._log(warning, 'warning')

        async def stage_failed(exc):
            message = f'{stage}: {exc}'
            errors.append(message)
            reports[-1].update(status='FAILED', errors=[message])
            save('RUNNING')
            await self._log(message + '; continuing subsequent stages.', 'warning')

        async def guarded(action, fallback=None):
            try:
                return action()
            except Exception as exc:
                await stage_failed(exc)
                return fallback

        async def complete_design(result, path):
            artifacts[stage] = str(path)
            if result.ok:
                await finish(path, result.warnings)
            else:
                await stage_failed('; '.join(result.errors))

        visual_executor = None
        visual_future = None
        visual_report = None
        try:
            from model.client import Model
            from .database_design import DatabaseSchemaPass
            from .backend_design import BackendDesignPass
            from .frontend_design import FrontendDesignPass
            from .project_initialization import ProjectInitializer
            from .database_lowering import DatabaseLoweringPass
            from .backend_lowering import BackendLoweringPass
            from .frontend_lowering import FrontendLoweringPass
            from .lowering.compilation import check_compilation
            from .implementation import NodeTDDOrchestrator
            from .build.stage import ProjectBuilder
            from .validation_preparation.stage import ValidationPreparationPass, check_validation_readiness
            from .stage_repair import repair_gate

            from cli.commands import _ensure_dotenv_loaded
            _ensure_dotenv_loaded()
            await begin('project_initialization')
            initialized = await guarded(lambda: (ProjectInitializer(output, web_port=request.web_port).initialize(), True)[1])
            if initialized:
                await finish(root / 'project/report.json')

            model = self.model if self.model is not None else Model.from_env()
            visual = FrontendDesignPass(model, root, reference_root=Path(request.requirement_path).resolve().parent)
            await begin('visual_analysis')
            visual_report = reports[-1]
            def analyze_images():
                seen = set()
                for rid in leaves:
                    for page in requirements['nodes'][rid].get('pages', []):
                        reference = (page.get('reference') or '').replace('\\', '/')
                        if reference and reference not in seen:
                            seen.add(reference)
                            try:
                                visual._analyze(page['name'], reference, rid)
                            except Exception as exc:
                                visual.visual_failures[reference] = str(exc)
                                visual.visual_log.warning(f'Skipping {reference}: {exc}', node_id=rid)
            # Separate thread: synchronous database design must not block image analysis.
            # One writer owns the visual cache; duplicate paths are analyzed once.
            visual_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='arc-visual')
            visual_future = visual_executor.submit(analyze_images)
            await self._log('Visual analysis is running in the background alongside database design.')

            await begin('database_design')
            from .database_design.state import DatabaseSchemaState
            from .database_design.refinement import refine_er_model
            empty_er = DatabaseSchemaState().to_er_model()
            database = await guarded(
                lambda: DatabaseSchemaPass(model, root).compile(requirements, parsed.dependency_graph),
                SimpleNamespace(schema=refine_er_model(empty_er, requirement_ids=set(leaves)).schema,
                    er_model=empty_er, node_states={rid: 'FAILED' for rid in leaves},
                    ok=False, errors=['Database design unavailable'], warnings=[]))
            await guarded(lambda: (
                write_json_atomic(root / 'design/database/schema.json', database.schema),
                write_json_atomic(root / 'design/database/er_model.json', database.er_model),
                write_json_atomic(root / 'design/database/report.json', {'status': database.schema['status'],
                    'node_states': database.node_states, 'errors': database.errors, 'warnings': database.warnings})))
            if reports[-1]['status'] == 'RUNNING':
                if database.schema['status'] != 'RESOLVED' and database.ok:
                    await stage_failed('Database design did not resolve its schema')
                else:
                    await complete_design(database, root / 'design/database/schema.json')

            from .lowering.recovery import isolate_design, bind_recovery, minimal_lowering
            recovery_reasons = {name: {} for name in ('database', 'backend', 'frontend')}

            async def lower(name, lowerer, *args):
                await begin(name + '_lowering')
                plan, effective = None, args[-1]
                def render():
                    render_args = (effective,) if name == 'database' else (requirements, effective)
                    return lowerer.compile(*render_args, output)
                def check():
                    try:
                        compilation = check_compilation(output, name)
                        return {'ok': compilation['status'] == 'COMPILED', 'compilation': compilation}
                    except Exception as exc:
                        return {'ok': False, 'errors': [str(exc)]}
                try:
                    plan = render()
                except Exception as exc:
                    await self._log(f'{name}: isolating unlowerable design: {exc}', 'warning')
                    try:
                        plan, effective = isolate_design(name, lowerer, requirements, effective, output)
                        recovery_reasons[name].update(plan.pop('recovery_reasons', {}))
                    except Exception as isolation_error:
                        recovery_reasons[name].update({rid: str(isolation_error)[:1000] for rid in leaves})
                        plan, effective = minimal_lowering(name, lowerer, requirements, output)
                result = check()
                if not result['ok']:
                    try:
                        result = repair_gate(model, output, name + '_lowering', check, result, contracts=effective)
                    except Exception as exc:
                        await self._log(f'{name}: lowering repair unavailable; using recovery scaffolding: {exc}', 'warning')
                if not result['ok']:
                    # The populated source plan still cannot compile. Replace only
                    # stage-owned scaffolding, preserving user-modified files.
                    recovery_reasons[name].update({rid: str(result)[:1000] for rid in leaves})
                    plan, effective = minimal_lowering(name, lowerer, requirements, output)
                    result = check()
                if result['ok']:
                    manifest_path = root / 'lowering' / name / 'manifest.json'
                    manifest = read_json(manifest_path, {})
                    from .lowering.files import digest
                    for relative in manifest.get('files', {}):
                        manifest['files'][relative] = digest((output / relative).read_text(encoding='utf-8'))
                    write_json_atomic(manifest_path, manifest)
                    await finish(root / 'lowering' / name / 'plan.json')
                else:
                    await stage_failed(name + ': minimal framework still needs repair: ' + str(result))
                if recovery_reasons[name]:
                    filename = 'schema.json' if name == 'database' else name + '_ir.json'
                    write_json_atomic(root / 'design' / name / filename, effective)
                return plan, effective

            database_plan, database.schema = await lower('database', DatabaseLoweringPass(), database.schema)
            await self._log('Waiting for visual evidence before backend design.')
            try:
                visual_future.result()
            except Exception as exc:
                visual.visual_failures['background_analysis'] = str(exc)
                visual.visual_log.warning('Background visual analysis skipped: ' + str(exc))
            visual_report.update(status='COMPLETED_WITH_WARNINGS' if visual.visual_failures else 'COMPLETED',
                                 warnings=[f'{path}: {message}' for path, message in visual.visual_failures.items()])
            artifacts['visual_analysis'] = str(root / 'visual_cache.json')
            save('RUNNING')
            await self._log('visual_analysis: COMPLETED')
            await begin('backend_design')
            backend = await guarded(lambda: BackendDesignPass(model, root).compile(requirements, database.er_model),
                SimpleNamespace(ir={'ir_kind': 'backend_design', 'types': [], 'modules': []},
                    node_states={rid: 'SKIPPED' for rid in leaves}, ok=False,
                    errors=['Backend design unavailable'], warnings=[]))
            if reports[-1]['status'] == 'RUNNING':
                await complete_design(backend, root / 'design/backend/backend_ir.json')
            backend_plan, backend.ir = await lower('backend', BackendLoweringPass(), requirements, backend.ir)
            write_json_atomic(root / 'design/backend/backend_ir.json', backend.ir)

            await begin('frontend_design')
            from .frontend_design.delta import make_ui
            frontend = await guarded(lambda: visual.compile(requirements, database.er_model, backend.ir),
                SimpleNamespace(ir={'ir_kind': 'frontend_design',
                    'ui': make_ui('UI_APP', 'Application', 'app', 'Application root.'),
                    'types': [], 'data': [], 'events': [], 'handlers': []},
                    node_states={rid: 'SKIPPED' for rid in leaves}, ok=False,
                    errors=['Frontend design unavailable'], warnings=[]))
            if reports[-1]['status'] == 'RUNNING':
                await complete_design(frontend, root / 'design/frontend/frontend_ir.json')
            frontend_plan, frontend.ir = await lower('frontend', FrontendLoweringPass(), requirements, frontend.ir)
            write_json_atomic(root / 'design/frontend/frontend_ir.json', frontend.ir)

            # FileContext derives requirement/file associations from the lowering
            # plans directly; no second binding registry is persisted.
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
                reports[-1].setdefault('warnings', []).append(message)
                save('RUNNING')
                await self._log(message, 'warning')
            if not request.stop_after_lowering:
                await begin('implementation')
                try:
                    implementation = NodeTDDOrchestrator(model, output, port=request.web_port).compile(
                        requirements, database.er_model, backend.ir, frontend.ir,
                        backend_plan, frontend_plan, database_plan)
                    states.update(implementation.node_states)
                    failed.extend(rid for rid in leaves if states[rid] not in {'IMPLEMENTED', 'ALREADY_PASSED'})
                    failed[:] = sorted(set(failed))
                    if implementation.ok and not failed:
                        await finish(root / 'implementation/report.json', implementation.warnings)
                    else:
                        raise ValueError('; '.join(implementation.errors) or 'Requirements not implemented: ' + ', '.join(failed))
                except Exception as exc:
                    message = 'implementation: ' + str(exc)
                    errors.append(message)
                    reports[-1].update(status='FAILED', errors=[message])
                    artifacts['implementation'] = str(root / 'implementation/report.json')
                    save('RUNNING')
                    await self._log(message + '; continuing to build repair.', 'warning')

            await begin('build')
            def build_check():
                try:
                    result = ProjectBuilder(output).build()
                    return {'ok': result.ok, 'errors': result.errors}
                except Exception as exc:
                    return {'ok': False, 'errors': [str(exc)]}
            build = build_check()
            if not build['ok']:
                build = await guarded(lambda: repair_gate(model, output, 'build', build_check, build), build)
            if build['ok']:
                await finish(output / 'frontend/dist/index.html')
            else:
                message = 'build: repair budget exhausted: ' + str(build)
                errors.append(message)
                reports[-1].update(status='FAILED', errors=[message])
                save('RUNNING')
                await self._log(message, 'warning')
            if request.prepare_validation and not request.stop_after_lowering and build['ok']:
                await begin('validation_preparation')
                def prepare_validation():
                    validation = ValidationPreparationPass(model, output).generate(requirements)
                    check_validation_readiness(output, validation=True)
                    write_json_atomic(root / 'validation_preparation/report.json',
                                      {'status': 'PREPARED', 'artifacts': validation, 'port': request.web_port})
                    return True
                prepared = await guarded(prepare_validation)
                if prepared:
                    await finish(root / 'validation_preparation/report.json')
            elif request.prepare_validation and not build['ok']:
                reports.append({'stage': 'validation_preparation', 'status': 'SKIPPED',
                                'warnings': ['Build unavailable; validation artifacts cannot be prepared.']})
            save('COMPLETED_WITH_ERRORS' if errors else 'LOWERED' if request.stop_after_lowering else 'COMPLETED')
            return CompilationResult(ok=not errors, root_id=requirements['root_id'], states=states,
                failed_nodes=failed, artifacts=artifacts, run_id=run_id, completed=True)
        except Exception as exc:
            errors.append(f'{stage}: {exc}')
            if reports:
                next(row for row in reversed(reports) if row['stage'] == stage).update(status='FAILED', errors=[str(exc)])
            if not failed:
                failed.extend(rid for rid in leaves if states[rid] not in {'IMPLEMENTED', 'ALREADY_PASSED'})
            save('FAILED')
            await self._log('FAILED: ' + errors[-1], 'error')
            return CompilationResult(ok=False, root_id=requirements['root_id'], states=states,
                failed_nodes=failed, artifacts=artifacts, run_id=run_id)
        finally:
            try:
                if visual_executor is not None:
                    visual_executor.shutdown(wait=True, cancel_futures=True)
                    if visual_future is not None and visual_report['status'] == 'RUNNING':
                        error = visual_future.exception() if not visual_future.cancelled() else 'Cancelled'
                        visual_report.update(status='FAILED' if error else 'COMPLETED',
                                             errors=[str(error)] if error else [])
                        save('COMPLETED_WITH_ERRORS' if errors or error else 'COMPLETED')
            finally:
                set_run_id(None)
