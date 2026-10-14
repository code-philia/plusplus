"""Workflow-only compiler. Domain implementations and IRs are intentionally absent."""
from __future__ import annotations
import inspect
import json
import uuid
from typing import Any
from .contracts import CompilationRequest, CompilationResult
from .preprocessing import RequirementPreprocessor
from .workflow import STAGES, WorkflowContext, plan_stage
from utils.jsonio import write_json_atomic


class Compiler:
    def __init__(self, runtime: Any = None, log_cb: Any = None, model: Any = None) -> None:
        self.log_cb = log_cb

    async def compile(self, request: CompilationRequest) -> CompilationResult:
        if request.resume or request.start_from != 'zero':
            raise ValueError('Resume/checkpoints must be redesigned; the shell starts from zero.')
        parsed = RequirementPreprocessor().compile(request.requirement_path)
        if not parsed.ok:
            raise ValueError('; '.join(parsed.errors))
        context = WorkflowContext(request.output_dir.resolve(), parsed.requirement_ir, parsed.dependency_graph)
        root = context.output_dir / '.arc'
        existing_path = root / 'preprocessing/requirement_ir.json'
        if existing_path.is_file():
            existing = json.loads(existing_path.read_text(encoding='utf-8'))
            if existing.get('source') != parsed.requirement_ir.get('source'):
                raise ValueError('Requirements differ from the existing workspace; use a new output directory.')
        write_json_atomic(root / 'preprocessing/requirement_ir.json', parsed.requirement_ir)
        write_json_atomic(root / 'preprocessing/dependency_graph.json', parsed.dependency_graph)
        artifacts, stages = {}, []
        for stage in STAGES:
            if stage == 'validation_preparation' and not request.prepare_validation:
                continue
            report, path = plan_stage(context, stage)
            stages.append(report)
            artifacts[stage] = str(path)
            if self.log_cb:
                result = self.log_cb('Compiler', f'{stage}: NOT_IMPLEMENTED (workflow placeholder)', None, None)
                if inspect.isawaitable(result):
                    await result
            if request.stop_after_lowering and stage == 'frontend_lowering':
                break
        summary = root / 'workflow/report.json'
        write_json_atomic(summary, {'status': 'WORKFLOW_PLANNED', 'implementation_status': 'NOT_IMPLEMENTED', 'stages': stages})
        artifacts['workflow'] = str(summary)
        # ok means the shell ran, not that an application was generated.
        return CompilationResult(ok=True, root_id=parsed.requirement_ir['root_id'],
            states={key: 'NOT_IMPLEMENTED' for key in parsed.requirement_ir['nodes']},
            artifacts=artifacts, run_id=uuid.uuid4().hex)
