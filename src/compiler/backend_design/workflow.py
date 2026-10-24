"""Backend design stage: inputs, transactional execution and usable fallback."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compiler.preprocessing.queries import leaf_ids
from compiler.stages import Stage, StageResult, StageStatus
from compiler.visual_analysis import VisualAnalysisOutput
from .stage import BackendDesignPass, BackendDesignResult


@dataclass(frozen=True, slots=True)
class BackendDesignInput:
    requirements: dict[str, Any]
    database: dict[str, Any]
    visual: VisualAnalysisOutput
    artifact_root: Path


class BackendDesignStage(Stage[BackendDesignResult]):
    def __init__(self, inputs: BackendDesignInput, model, *, runner=None):
        super().__init__('backend_design', runner=runner)
        self.inputs, self.model = inputs, model

    async def _execute(self):
        inputs = self.inputs
        try:
            result = BackendDesignPass(self.model, inputs.artifact_root, visual_result=inputs.visual).compile(
                inputs.requirements, inputs.database)
        except Exception as exc:
            result = BackendDesignResult({'ir_kind': 'backend_design', 'types': [], 'modules': []},
                {rid: 'SKIPPED' for rid in leaf_ids(inputs.requirements)}, errors=['Backend design unavailable'])
            return StageResult(StageStatus.DEGRADED, result, errors=(str(exc),))
        return StageResult(StageStatus.SUCCEEDED if result.ok else StageStatus.DEGRADED, result,
            errors=() if result.ok else ('; '.join(result.errors),),
            warnings=tuple(result.warnings) if result.ok else (),
            artifact=inputs.artifact_root / 'design/backend/backend_ir.json')
