from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .modules import ModuleSkeletonLowerer
from ..planning.files import GlobalFilePlanner
from ..planning.symbols import GlobalSymbolPlanner
from ..rules.skeleton import TypeLowerer
from .lower import BackendGlueLowerer
from ..database.fixtures import fixture_source_paths


@dataclass(slots=True)
class BackendSourceResult:
    symbol_planning: Any | None = None
    file_planning: Any | None = None
    type_lowering: Any | None = None
    db_modules: Any | None = None
    func_modules: Any | None = None
    api_modules: Any | None = None
    backend_glue: Any | None = None
    failed_phase: str | None = None
    errors: list[str] | None = None
    warnings: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []

    @property
    def ok(self) -> bool:
        return self.failed_phase is None and not self.errors


class BackendSourcePipeline:
    """Run legacy-compatible backend source lowering in explicit order."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def run(
        self,
        *,
        design_ir: dict[str, Any],
        database_schema: dict[str, Any],
        fixture_ir: dict[str, Any],
        project_manifest: dict[str, Any],
        database_manifest: dict[str, Any],
        default_port: int,
    ) -> BackendSourceResult:
        result = BackendSourceResult()
        result.symbol_planning = GlobalSymbolPlanner().plan(
            design_ir, database_schema, project_manifest
        )
        if not result.symbol_planning.ok:
            result.warnings.extend(result.symbol_planning.errors)

        result.file_planning = GlobalFilePlanner(self.output_dir).plan(
            design_ir,
            result.symbol_planning.registry,
            project_manifest,
            fixture_paths=fixture_source_paths(fixture_ir),
        )
        if not result.file_planning.ok:
            result.failed_phase = "GLOBAL_FILE_PLANNING"
            result.errors.extend(result.file_planning.errors)
            return result

        result.type_lowering = TypeLowerer().lower(
            result.symbol_planning.registry, result.file_planning.registry
        )
        if not result.type_lowering.ok:
            result.failed_phase = "TYPE_LOWERING"
            result.errors.extend(result.type_lowering.errors)
            return result

        module_lowerer = ModuleSkeletonLowerer()
        result.db_modules = module_lowerer.lower(
            "DB", design_ir, result.symbol_planning.registry, result.file_planning.registry
        )
        if not result.db_modules.ok:
            result.failed_phase = "DB_MODULE_LOWERING"
            result.errors.extend(result.db_modules.errors)
            return result

        result.func_modules = module_lowerer.lower(
            "FUNC", design_ir, result.symbol_planning.registry, result.file_planning.registry
        )
        if not result.func_modules.ok:
            result.failed_phase = "FUNC_MODULE_LOWERING"
            result.errors.extend(result.func_modules.errors)
            return result

        result.api_modules = module_lowerer.lower(
            "API", design_ir, result.symbol_planning.registry, result.file_planning.registry
        )
        if not result.api_modules.ok:
            result.failed_phase = "API_MODULE_LOWERING"
            result.errors.extend(result.api_modules.errors)
            return result

        result.backend_glue = BackendGlueLowerer().lower(
            design_ir,
            result.symbol_planning.registry,
            result.file_planning.registry,
            {
                "type": result.type_lowering.manifest,
                "database": database_manifest,
                "DB": result.db_modules.manifest,
                "FUNC": result.func_modules.manifest,
                "API": result.api_modules.manifest,
            },
            default_port=default_port,
        )
        if not result.backend_glue.ok:
            result.failed_phase = "GLOBAL_GLUE_LOWERING"
            result.errors.extend(result.backend_glue.errors)
        return result


__all__ = ["BackendSourcePipeline", "BackendSourceResult"]
