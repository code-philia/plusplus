"""Frontend design consumes settled visual evidence, never a background executor."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compiler.preprocessing.queries import leaf_ids
from compiler.stages import Stage, StageResult, StageStatus
from compiler.visual_analysis import VisualAnalysisOutput
from .delta import make_ui
from .stage import FrontendDesignPass, FrontendDesignResult


@dataclass(frozen=True, slots=True)
class FrontendDesignInput:
    requirements: dict[str, Any]
    database: dict[str, Any]
    backend: dict[str, Any]
    visual: VisualAnalysisOutput
    artifact_root: Path


class FrontendDesignStage(Stage[FrontendDesignResult]):
    def __init__(self, inputs: FrontendDesignInput, model, *, runner=None):
        super().__init__('frontend_design', runner=runner)
        self.inputs, self.model = inputs, model

    async def _execute(self):
        inputs = self.inputs
        try:
            result = FrontendDesignPass(self.model, inputs.artifact_root, visual_result=inputs.visual).compile(
                inputs.requirements, inputs.database, inputs.backend)
        except Exception as exc:
            empty = {'ir_kind': 'frontend_design',
                'ui': make_ui('UI_APP', 'Application', 'app', 'Application root.'),
                'types': [], 'data': [], 'events': [], 'handlers': []}
            result = FrontendDesignResult(empty, [], {rid: 'SKIPPED' for rid in leaf_ids(inputs.requirements)},
                                          errors=['Frontend design unavailable'])
            return StageResult(StageStatus.DEGRADED, result, errors=(str(exc),))
        return StageResult(StageStatus.SUCCEEDED if result.ok else StageStatus.DEGRADED, result,
            errors=() if result.ok else ('; '.join(result.errors),),
            warnings=tuple(result.warnings) if result.ok else (),
            artifact=inputs.artifact_root / 'design/frontend/frontend_ir.json')
