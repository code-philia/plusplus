"""Validation artifacts, readiness and persisted launch configuration."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compiler.stages import Stage, StageResult, StageStatus
from utils.jsonio import write_json_atomic
from .stage import ValidationPreparationPass, check_validation_readiness


@dataclass(frozen=True, slots=True)
class ValidationPreparationInput:
    requirements: dict[str, Any]
    output_root: Path
    web_port: int


@dataclass(frozen=True, slots=True)
class ValidationPreparationOutput:
    artifacts: dict[str, str]
    report_path: Path


class ValidationPreparationStage(Stage[ValidationPreparationOutput]):
    def __init__(self, inputs: ValidationPreparationInput, model, *, runner=None):
        super().__init__('validation_preparation', runner=runner)
        self.inputs, self.model = inputs, model

    async def _execute(self):
        inputs = self.inputs
        try:
            artifacts = ValidationPreparationPass(self.model, inputs.output_root).generate(inputs.requirements)
            check_validation_readiness(inputs.output_root, validation=True)
            path = inputs.output_root / '.arc/validation_preparation/report.json'
            write_json_atomic(path, {'status': 'PREPARED', 'artifacts': artifacts, 'port': inputs.web_port})
        except Exception as exc:
            return StageResult(StageStatus.FAILED, errors=(str(exc),))
        return StageResult(StageStatus.SUCCEEDED, ValidationPreparationOutput(artifacts, path), artifact=path)
