"""Production build and bounded repair belong to the build stage."""
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from agent.tools import IndeterminateToolCall

from compiler.stage_repair import repair_gate
from compiler.stages import Stage, StageResult, StageStatus
from .stage import ProjectBuilder


@dataclass(frozen=True, slots=True)
class BuildOutput:
    ok: bool
    evidence: dict[str, Any]


class BuildStage(Stage[BuildOutput]):
    def __init__(self, output_root: Path, model, budgets, *, runner=None):
        super().__init__('build', runner=runner)
        self.output_root, self.model, self.budgets = output_root, model, budgets

    def check(self):
        try:
            result = ProjectBuilder(self.output_root).build()
            return {'ok': result.ok, 'errors': result.errors}
        except Exception as exc:
            return {'ok': False, 'errors': [str(exc)]}

    async def _execute(self):
        evidence = self.check()
        errors = []
        if not evidence['ok']:
            try:
                evidence = repair_gate(self.model, self.output_root, 'build', self.check, evidence,
                                       budgets=replace(self.budgets, model_attempts=1))
            except IndeterminateToolCall:
                raise
            except Exception as exc:
                errors.append(str(exc))
        if not evidence['ok']:
            errors.append('repair budget exhausted: ' + str(evidence))
        return StageResult(StageStatus.DEGRADED if errors else StageStatus.SUCCEEDED,
            BuildOutput(evidence['ok'], evidence), errors=tuple(errors),
            artifact=self.output_root / 'frontend/dist/index.html' if evidence['ok'] else None)
