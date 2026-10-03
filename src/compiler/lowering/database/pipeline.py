from __future__ import annotations

from pathlib import Path
from typing import Any

from compiler.driver.artifacts import CompilerArtifactStore
from .lower import DatabaseLoweringResult, lower_database


class DatabaseSourcePipeline:
    """Run database schema and fixture source lowering behind a stable boundary."""

    def run(
        self,
        *,
        output_dir: Path,
        artifact_store: CompilerArtifactStore,
        schema: dict[str, Any],
        fixture_ir: dict[str, Any],
        project_manifest: dict[str, Any],
    ) -> DatabaseLoweringResult:
        return lower_database(
            output_dir, artifact_store, schema, fixture_ir, project_manifest
        )


__all__ = ["DatabaseSourcePipeline"]
