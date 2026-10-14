"""Execute the current design, lowering, implementation and validation stages."""
from __future__ import annotations

import inspect
import uuid
from pathlib import Path
from typing import Any

from .contracts import CompilationRequest, CompilationResult
from .preprocessing import RequirementPreprocessor
from .preprocessing.traversal import children_by_id, postorder
from utils.jsonio import read_json, write_json_atomic


class Compiler:
    def __init__(self, runtime: Any = None, log_cb: Any = None, model: Any = None) -> None:
        self.log_cb = log_cb
        self.model = model

    async def _log(self, message):
        if self.log_cb:
            result = self.log_cb('Compiler', message, None, None)
            if inspect.isawaitable(result):
                await result
        else:
            print('[Compiler] ' + message, flush=True)

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
                await self._log('WARNING: ' + warning)

        def require_result(result):
            if not result.ok:
                raise ValueError('; '.join(result.errors))

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

            from cli.commands import _ensure_dotenv_loaded
            _ensure_dotenv_loaded()
            await begin('project_initialization')
            ProjectInitializer(output, web_port=request.web_port).initialize()
            await finish(root / 'project/report.json')

            model = self.model if self.model is not None else Model.from_env()
            visual = FrontendDesignPass(model, root, reference_root=Path(request.requirement_path).resolve().parent)
            await begin('visual_analysis')
            for rid in leaves:
                for page in requirements['nodes'][rid].get('pages', []):
                    if page.get('reference'):
                        visual._analyze(page['name'], page['reference'], rid)
            # Backend/database contexts can use the same evidence before frontend tree design.
            write_json_atomic(root / 'visual_cache.json', visual.visual_cache)
            await finish(root / 'design/frontend/visual_cache.json')

            await begin('database_design')
            database = DatabaseSchemaPass(model, root).compile(requirements, parsed.dependency_graph)
            write_json_atomic(root / 'design/database/schema.json', database.schema)
            write_json_atomic(root / 'design/database/er_model.json', database.er_model)
            write_json_atomic(root / 'design/database/report.json', {'status': database.schema['status'],
                'node_states': database.node_states, 'errors': database.errors, 'warnings': database.warnings})
            require_result(database)
            if database.schema['status'] != 'RESOLVED':
                raise ValueError('Database design did not resolve its schema')
            await finish(root / 'design/database/schema.json', database.warnings)

            async def lower(name, lowerer, *args):
                await begin(name + '_lowering')
                plan = lowerer.compile(*args, output)
                compilation = check_compilation(output, name)
                if compilation['status'] != 'COMPILED':
                    raise ValueError(str(compilation))
                await finish(root / 'lowering' / name / 'plan.json')
                return plan

            database_plan = await lower('database', DatabaseLoweringPass(), database.schema)
            await begin('backend_design')
            backend = BackendDesignPass(model, root).compile(requirements, database.er_model)
            require_result(backend)
            await finish(root / 'design/backend/backend_ir.json', backend.warnings)
            backend_plan = await lower('backend', BackendLoweringPass(), requirements, backend.ir)

            await begin('frontend_design')
            frontend = visual.compile(requirements, database.er_model, backend.ir)
            require_result(frontend)
            await finish(root / 'design/frontend/frontend_ir.json', frontend.warnings)
            frontend_plan = await lower('frontend', FrontendLoweringPass(), requirements, frontend.ir)

            await begin('code_binding')
            binding = {'ir_kind': 'code_binding', 'requirements': {}}
            for rid in leaves:
                associated = {row['file_path'] for row in backend_plan['modules'] if rid in row['reqs']}
                associated.update(row['file_path'] for row in frontend_plan['components'] + frontend_plan['handlers']
                                  if rid in row['reqs'])
                binding['requirements'][rid] = sorted(associated)
                states[rid] = 'LOWERED'
            write_json_atomic(root / 'code_binding/bindings.json', binding)
            await finish(root / 'code_binding/bindings.json')

            skipped = {rid for result in (backend, frontend) for rid, state in result.node_states.items()
                       if state == 'SKIPPED'}
            if skipped:
                failed.extend(sorted(skipped))
                for rid in skipped:
                    states[rid] = 'DESIGN_FAILED'
                raise ValueError('Design exhausted its attempts for: ' + ', '.join(sorted(skipped)))
            if not request.stop_after_lowering:
                await begin('implementation')
                implementation = NodeTDDOrchestrator(model, output, port=request.web_port).compile(
                    requirements, database.er_model, backend.ir, frontend.ir,
                    backend_plan, frontend_plan, database_plan)
                states.update(implementation.node_states)
                failed.extend(rid for rid in leaves if states[rid] not in {'IMPLEMENTED', 'ALREADY_PASSED'})
                require_result(implementation)
                if failed:
                    raise ValueError('Requirements not implemented: ' + ', '.join(failed))
                await finish(root / 'implementation/report.json', implementation.warnings)

            await begin('build')
            require_result(ProjectBuilder(output).build())
            await finish(output / 'frontend/dist/index.html')
            if request.prepare_validation and not request.stop_after_lowering:
                await begin('validation_preparation')
                validation = ValidationPreparationPass(model, output).generate(requirements)
                check_validation_readiness(output, validation=True)
                write_json_atomic(root / 'validation_preparation/report.json',
                                  {'status': 'PREPARED', 'artifacts': validation, 'port': request.web_port})
                await finish(root / 'validation_preparation/report.json')
            save('LOWERED' if request.stop_after_lowering else 'COMPLETED')
            return CompilationResult(ok=True, root_id=requirements['root_id'], states=states,
                artifacts=artifacts, run_id=run_id)
        except Exception as exc:
            errors.append(f'{stage}: {exc}')
            if reports:
                reports[-1].update(status='FAILED', errors=[str(exc)])
            if not failed:
                failed.extend(rid for rid in leaves if states[rid] not in {'IMPLEMENTED', 'ALREADY_PASSED'})
            save('FAILED')
            await self._log('FAILED: ' + errors[-1])
            return CompilationResult(ok=False, root_id=requirements['root_id'], states=states,
                failed_nodes=failed, artifacts=artifacts, run_id=run_id)
