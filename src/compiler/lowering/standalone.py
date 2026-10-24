"""Standalone lowering owns local recovery binding, not another rendering loop."""
from dataclasses import dataclass, replace
from pathlib import Path

from compiler.backend_lowering.workflow import BackendLoweringInput, BackendLoweringStage
from compiler.database_lowering.workflow import DatabaseLoweringInput, DatabaseLoweringStage
from compiler.frontend_lowering.workflow import FrontendLoweringInput, FrontendLoweringStage
from compiler.preprocessing.queries import leaf_ids
from compiler.stages import Stage, StageResult, StageStatus
from utils.jsonio import read_json, read_json_object, write_json_atomic
from .recovery import bind_recovery
from .workflow import LoweringOutput


@dataclass(frozen=True, slots=True)
class StandaloneLoweringInput:
    layer: str
    output_root: Path
    component_depth: int = 2
    min_component_nodes: int = 4


class StandaloneLoweringStage(Stage[LoweringOutput]):
    def __init__(self, inputs: StandaloneLoweringInput, budgets, *, runner=None):
        if inputs.layer not in {'database', 'backend', 'frontend'}:
            raise ValueError(f'Unknown lowering layer: {inputs.layer}')
        super().__init__('standalone_' + inputs.layer + '_lowering', runner=runner)
        self.inputs, self.budgets = inputs, budgets

    async def _execute(self):
        inputs = self.inputs
        layer, output = inputs.layer, inputs.output_root
        arc = output / '.arc'
        requirements = read_json_object(arc / 'preprocessing/requirement_ir.json')
        filename = 'schema.json' if layer == 'database' else layer + '_ir.json'
        design = read_json_object(arc / 'design' / layer / filename)
        if layer != 'database' and not (output / layer / 'package.json').is_file():
            raise ValueError(f'Initialize {layer}/package.json before lowering')
        if layer == 'database':
            stage = DatabaseLoweringStage(DatabaseLoweringInput(requirements, design, output), None, self.budgets)
        elif layer == 'backend':
            stage = BackendLoweringStage(BackendLoweringInput(requirements, design, output), None, self.budgets)
        else:
            stage = FrontendLoweringStage(FrontendLoweringInput(requirements, design, output), None, self.budgets,
                component_depth=inputs.component_depth, min_nodes=inputs.min_component_nodes)
        result = await stage.run()
        lowered = result.require_value()
        reasons = dict(lowered.recovery_reasons)
        if layer == 'database':
            write_json_atomic(arc / 'design/database/schema.json', lowered.effective_design)
            if reasons:
                lowered.plan['recovery_requirements'] = reasons
                write_json_atomic(arc / 'lowering/database/plan.json', lowered.plan)
                backend = read_json(arc / 'lowering/backend/plan.json', {})
                if backend:
                    bind_recovery(output, 'backend', backend, reasons)
        else:
            if layer == 'backend':
                reasons.update(read_json(arc / 'lowering/database/plan.json', {}).get('recovery_requirements', {}))
            states = read_json(arc / 'design' / layer / 'report.json', {}).get('node_states', {})
            for rid in leaf_ids(requirements):
                if states.get(rid) in {'FAILED', 'SKIPPED'} and (layer == 'backend' or requirements['nodes'][rid].get('pages')):
                    reasons[rid] = f'{layer} design incomplete; implement from the requirement.'
            plan = bind_recovery(output, layer, lowered.plan, reasons)
            lowered = replace(lowered, plan=plan, recovery_reasons=reasons)
        status = StageStatus.DEGRADED if reasons and result.status == StageStatus.SUCCEEDED else result.status
        return StageResult(status, lowered, result.diagnostics, result.errors, result.warnings, result.artifact)
