"""Basic stages: own their inputs, validation and persisted outputs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.jsonio import read_json, write_json_atomic

from .contracts import CompilationRequest
from .preprocessing import RequirementPreprocessor
from .preprocessing.queries import leaf_ids
from .stages import Stage, StageResult, StageRunner, StageStatus


@dataclass(frozen=True, slots=True)
class PreprocessingOutput:
    requirement_ir: dict[str, Any]
    dependency_graph: dict[str, Any]
    output_root: Path
    states: dict[str, str]
    artifacts: dict[str, str]


class PreprocessingStage(Stage[PreprocessingOutput]):
    def __init__(self, request: CompilationRequest, *, runner: StageRunner | None = None) -> None:
        super().__init__('preprocessing', runner=runner, reported=False)
        self.request = request

    async def _execute(self) -> StageResult[PreprocessingOutput]:
        parsed = RequirementPreprocessor().compile(self.request.requirement_path)
        if not parsed.ok:
            raise ValueError('; '.join(parsed.errors))
        output = self.request.output_dir.expanduser().resolve()
        root = output / '.arc'
        requirements = parsed.requirement_ir
        leaves = leaf_ids(requirements)
        states = {rid: 'PENDING' if rid in leaves else 'ORGANIZATIONAL' for rid in requirements['nodes']}
        existing = read_json(root / 'preprocessing/requirement_ir.json', {})
        if existing and existing.get('source') != requirements.get('source'):
            raise ValueError('Requirements differ from the existing workspace; use a new output directory.')
        path = root / 'preprocessing/requirement_ir.json'
        write_json_atomic(path, requirements)
        write_json_atomic(root / 'preprocessing/dependency_graph.json', parsed.dependency_graph)
        return StageResult(StageStatus.SUCCEEDED, PreprocessingOutput(
            requirements, parsed.dependency_graph, output, states, {'requirements': str(path)}))


@dataclass(frozen=True, slots=True)
class InitializationInput:
    output_root: Path
    web_port: int = 3000
    install_dependencies: bool = True


@dataclass(frozen=True, slots=True)
class InitializationOutput:
    report: dict[str, Any]
    report_path: Path


class ProjectInitializationStage(Stage[InitializationOutput]):
    def __init__(self, inputs: InitializationInput, *, runner: StageRunner | None = None,
                 continue_on_error: bool = False) -> None:
        super().__init__('project_initialization', runner=runner)
        self.inputs = inputs
        self.continue_on_error = continue_on_error

    async def _execute(self) -> StageResult[InitializationOutput]:
        from .project_initialization import ProjectInitializer
        try:
            initializer = ProjectInitializer(self.inputs.output_root, web_port=self.inputs.web_port)
            report = initializer.initialize(install_dependencies=self.inputs.install_dependencies)
        except Exception as exc:
            if not self.continue_on_error:
                raise
            return StageResult(StageStatus.FAILED, errors=(str(exc),))
        path = self.inputs.output_root / '.arc/project/report.json'
        return StageResult(StageStatus.SUCCEEDED, InitializationOutput(report, path), artifact=path)
