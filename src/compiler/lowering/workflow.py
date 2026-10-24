"""Shared lowering lifecycle; concrete policies own rendering signatures."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compiler.preprocessing.queries import leaf_ids
from compiler.stages import StageResult, StageStatus
from compiler.stage_repair import repair_gate
from utils.jsonio import read_json, write_json_atomic
from utils.logging import SynchronousLog
from .compilation import check_compilation
from .files import digest
from .recovery import isolate_design, minimal_lowering


@dataclass(frozen=True, slots=True)
class LoweringInput:
    requirements: dict[str, Any]
    design: dict[str, Any]
    output_root: Path


@dataclass(frozen=True, slots=True)
class LoweringOutput:
    plan: dict[str, Any]
    effective_design: dict[str, Any]
    recovery_reasons: dict[str, str]
    evidence: dict[str, Any]
    original_design: dict[str, Any]


class LoweringPolicy:
    key: str
    design_filename: str
    persist_effective = True

    def __init__(self, inputs, lowerer):
        self.inputs, self.lowerer = inputs, lowerer

    def render(self, design):
        raise NotImplementedError

    def isolate(self, design):
        return isolate_design(self.key, self.lowerer, self.inputs.requirements, design, self.inputs.output_root)

    def minimal(self):
        return minimal_lowering(self.key, self.lowerer, self.inputs.requirements, self.inputs.output_root)


class LoweringWorkflow:
    def __init__(self, inputs: LoweringInput, policy: LoweringPolicy, model, budgets):
        self.inputs, self.policy, self.model, self.budgets = inputs, policy, model, budgets
        self.log = SynchronousLog('Compiler', workspace_root=inputs.output_root)

    def check(self):
        try:
            compilation = check_compilation(self.inputs.output_root, self.policy.key)
            return {'ok': compilation['status'] == 'COMPILED', 'compilation': compilation}
        except Exception as exc:
            return {'ok': False, 'errors': [str(exc)]}

    def run(self, output_type=LoweringOutput):
        inputs, policy = self.inputs, self.policy
        output, name = inputs.output_root, policy.key
        root = output / '.arc'
        leaves = leaf_ids(inputs.requirements)
        effective = inputs.design
        reasons = {}
        try:
            plan = policy.render(effective)
        except Exception as exc:
            self.log.warning(f'{name}: isolating unlowerable design: {exc}')
            try:
                plan, effective = policy.isolate(effective)
                reasons.update(plan.pop('recovery_reasons', {}))
            except Exception as isolation_error:
                reasons.update({rid: str(isolation_error)[:1000] for rid in leaves})
                plan, effective = policy.minimal()
        evidence = self.check()
        if not evidence['ok']:
            try:
                evidence = repair_gate(self.model, output, name + '_lowering', self.check, evidence,
                                       contracts=effective, budgets=self.budgets)
            except Exception as exc:
                self.log.warning(f'{name}: lowering repair unavailable; using recovery scaffolding: {exc}')
        if not evidence['ok']:
            reasons.update({rid: str(evidence)[:1000] for rid in leaves})
            plan, effective = policy.minimal()
            evidence = self.check()
        path = root / 'lowering' / name / 'plan.json'
        errors = ()
        if evidence['ok']:
            manifest_path = root / 'lowering' / name / 'manifest.json'
            manifest = read_json(manifest_path, {})
            for relative in manifest.get('files', {}):
                manifest['files'][relative] = digest((output / relative).read_text(encoding='utf-8'))
            write_json_atomic(manifest_path, manifest)
        else:
            errors = (name + ': minimal framework still needs repair: ' + str(evidence),)
        if reasons or policy.persist_effective:
            write_json_atomic(root / 'design' / name / policy.design_filename, effective)
        result = output_type(plan, effective, reasons, evidence, inputs.design)
        return StageResult(StageStatus.DEGRADED if reasons or errors else StageStatus.SUCCEEDED,
                           result, errors=errors, artifact=path if not errors else None)
