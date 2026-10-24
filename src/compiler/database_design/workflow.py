"""Database design owns fallback contracts and design artifacts."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compiler.preprocessing.queries import leaf_ids
from compiler.stages import Stage, StageResult, StageStatus
from utils.jsonio import write_json_atomic
from .stage import DatabaseSchemaPass, DatabasePassResult
from .state import DatabaseSchemaState
from .refinement import refine_er_model


@dataclass(frozen=True, slots=True)
class DatabaseDesignInput:
    requirements: dict[str, Any]
    dependency_graph: dict[str, Any]
    artifact_root: Path


class DatabaseDesignStage(Stage[DatabasePassResult]):
    def __init__(self, inputs: DatabaseDesignInput, model, *, runner=None):
        super().__init__('database_design', runner=runner)
        self.inputs, self.model = inputs, model

    async def _execute(self):
        inputs = self.inputs
        leaves = leaf_ids(inputs.requirements)
        empty = DatabaseSchemaState().to_er_model()
        fallback = DatabasePassResult(refine_er_model(empty, requirement_ids=set(leaves)).schema,
            {rid: 'FAILED' for rid in leaves}, empty, errors=['Database design unavailable'])
        errors = []
        try:
            result = DatabaseSchemaPass(self.model, inputs.artifact_root).compile(
                inputs.requirements, inputs.dependency_graph)
        except Exception as exc:
            result = fallback
            errors.append(str(exc))
        path = inputs.artifact_root / 'design/database/schema.json'
        try:
            write_json_atomic(path, result.schema)
            write_json_atomic(path.parent / 'er_model.json', result.er_model)
            write_json_atomic(path.parent / 'report.json', {'status': result.schema['status'],
                'node_states': result.node_states, 'errors': result.errors, 'warnings': result.warnings})
        except Exception as exc:
            errors.append(str(exc))
        artifact = None if errors else path
        if not errors:
            if result.schema['status'] != 'RESOLVED' and result.ok:
                errors.append('Database design did not resolve its schema')
                artifact = None
            elif not result.ok:
                errors.append('; '.join(result.errors))
        return StageResult(StageStatus.DEGRADED if errors else StageStatus.SUCCEEDED, result,
            errors=tuple(errors), warnings=tuple(result.warnings) if not errors else (), artifact=artifact)
