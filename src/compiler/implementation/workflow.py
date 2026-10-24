"""Recovery preparation always runs; TDD execution is a separate optional stage."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compiler.database_design.stage import DatabasePassResult
from compiler.backend_design.stage import BackendDesignResult
from compiler.frontend_design.stage import FrontendDesignResult
from compiler.frontend_design.validation import ui_index
from compiler.lowering.workflow import LoweringOutput
from compiler.lowering.recovery import bind_recovery
from compiler.preprocessing.queries import leaf_ids
from compiler.stages import Stage, StageResult, StageStatus
from .stage import NodeTDDOrchestrator


@dataclass(frozen=True, slots=True)
class PreparationInput:
    requirements: dict[str, Any]
    output_root: Path
    database: DatabasePassResult
    backend: BackendDesignResult
    frontend: FrontendDesignResult
    database_lowering: LoweringOutput
    backend_lowering: LoweringOutput
    frontend_lowering: LoweringOutput


@dataclass(frozen=True, slots=True)
class PreparedImplementation:
    requirements: dict[str, Any]
    er_model: dict[str, Any]
    backend_ir: dict[str, Any]
    frontend_ir: dict[str, Any]
    database_plan: dict[str, Any]
    backend_plan: dict[str, Any]
    frontend_plan: dict[str, Any]
    node_states: dict[str, str]


class ImplementationPreparationStage(Stage[PreparedImplementation]):
    def __init__(self, inputs: PreparationInput, *, runner=None):
        super().__init__('implementation_preparation', runner=runner, reported=False,
                         warning_target='frontend_lowering')
        self.inputs = inputs

    async def _execute(self):
        inputs = self.inputs
        requirements = inputs.requirements
        leaves = leaf_ids(requirements)
        for lowered in (inputs.database_lowering, inputs.backend_lowering, inputs.frontend_lowering):
            if not isinstance(lowered.plan, dict):
                raise ValueError('Implementation preparation requires a lowering plan')
        backend_ir = inputs.backend_lowering.effective_design
        frontend_ir = inputs.frontend_lowering.effective_design
        backend_recovery = dict(inputs.backend_lowering.recovery_reasons)
        frontend_recovery = dict(inputs.frontend_lowering.recovery_reasons)
        ui_nodes, _ = ui_index(frontend_ir['ui'])
        backend_owners = {rid for row in backend_ir['modules'] for rid in row['reqs']}
        ui_owners = {rid for row in ui_nodes.values() for rid in row['reqs']}
        for rid in leaves:
            if inputs.backend.node_states.get(rid) in {'SKIPPED', 'FAILED'}:
                backend_recovery[rid] = 'Backend design unavailable; implement from the requirement.'
            if (inputs.database.node_states.get(rid) in {'FAILED', 'SKIPPED'}
                    or rid in inputs.database_lowering.recovery_reasons):
                backend_recovery[rid] = 'Database design/lowering incomplete; repair shared initialization as needed.'
            if inputs.frontend.node_states.get(rid) in {'SKIPPED', 'FAILED'} and requirements['nodes'][rid].get('pages'):
                frontend_recovery[rid] = 'Frontend design unavailable; implement the declared pages from the requirement.'
            if requirements['nodes'][rid].get('pages') and rid not in ui_owners:
                frontend_recovery.setdefault(rid, 'No UI files bound; implement the declared pages from the requirement.')
            if rid not in backend_owners and rid not in ui_owners and rid not in frontend_recovery:
                backend_recovery.setdefault(rid, 'No source files bound; implement from the requirement.')
            if rid in backend_recovery and requirements['nodes'][rid].get('pages'):
                frontend_recovery.setdefault(rid, 'Integrate the recovered backend behavior with the declared pages.')
        backend_plan = bind_recovery(inputs.output_root, 'backend', inputs.backend_lowering.plan, backend_recovery)
        frontend_plan = bind_recovery(inputs.output_root, 'frontend', inputs.frontend_lowering.plan, frontend_recovery)
        recovered = set(backend_recovery) | set(frontend_recovery)
        states = {rid: 'LOWERED_WITH_RECOVERY' if rid in recovered else 'LOWERED' for rid in leaves}
        warnings = ()
        if recovered:
            warnings = ('Recovery files bound; continuing implementation for: ' + ', '.join(sorted(recovered)),)
        result = PreparedImplementation(requirements, inputs.database.er_model, backend_ir, frontend_ir,
            inputs.database_lowering.plan, backend_plan, frontend_plan, states)
        return StageResult(StageStatus.SUCCEEDED, result, warnings=warnings)


@dataclass(frozen=True, slots=True)
class ImplementationInput:
    prepared: PreparedImplementation
    output_root: Path
    web_port: int


@dataclass(frozen=True, slots=True)
class ImplementationOutput:
    node_states: dict[str, str]
    failed_nodes: tuple[str, ...]


class ImplementationStage(Stage[ImplementationOutput]):
    def __init__(self, inputs: ImplementationInput, model, budgets, *, runner=None):
        super().__init__('implementation', runner=runner)
        self.inputs, self.model, self.budgets = inputs, model, budgets

    async def _execute(self):
        inputs = self.inputs
        prepared = inputs.prepared
        states, failed, warnings = {}, (), ()
        path = inputs.output_root / '.arc/implementation/report.json'
        try:
            result = NodeTDDOrchestrator(self.model, inputs.output_root, port=inputs.web_port,
                budgets=self.budgets).compile(prepared.requirements, prepared.er_model,
                prepared.backend_ir, prepared.frontend_ir, prepared.backend_plan,
                prepared.frontend_plan, prepared.database_plan)
            states.update(result.node_states)
            effective_states = {**prepared.node_states, **states}
            failed = tuple(sorted(rid for rid in leaf_ids(prepared.requirements)
                                  if effective_states[rid] not in {'IMPLEMENTED', 'ALREADY_PASSED'}))
            if not result.ok or failed:
                raise ValueError('; '.join(result.errors) or 'Requirements not implemented: ' + ', '.join(failed))
            warnings = tuple(result.warnings)
        except Exception as exc:
            return StageResult(StageStatus.DEGRADED, ImplementationOutput(states, failed),
                               errors=(str(exc),), artifact=path)
        return StageResult(StageStatus.SUCCEEDED, ImplementationOutput(states, failed),
                           warnings=warnings, artifact=path)
