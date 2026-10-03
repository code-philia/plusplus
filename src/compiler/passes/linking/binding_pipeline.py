from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...fixture_lowering import fixture_source_paths
from ...lowering.backend.modules import ModuleSkeletonLowerer
from ...lowering.planning.files import GlobalFilePlanner
from ...lowering.planning.symbols import GlobalSymbolPlanner
from ...lowering.rules.skeleton import TypeLowerer
from .code_binding import CodeBindingLowerer, CodeBindingResult


class CodeBindingPipeline:
    """Rebuild binding manifests from the frozen lowering surfaces."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def run(
        self,
        *,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
        database_schema: dict[str, Any],
        design_ir: dict[str, Any],
        frontend_ir: dict[str, Any],
        fixture_ir: dict[str, Any],
        project_manifest: dict[str, Any],
        backend_routes: dict[str, Any],
        frontend_lowering_report_path: Path,
    ) -> CodeBindingResult:
        symbols = self._checked(GlobalSymbolPlanner().plan(
            design_ir, database_schema, project_manifest
        ))
        files = self._checked(GlobalFilePlanner(self.output_root).plan(
            design_ir,
            symbols.registry,
            project_manifest,
            fixture_paths=fixture_source_paths(fixture_ir),
        ))
        types = self._checked(TypeLowerer().lower(
            symbols.registry, files.registry
        ))
        modules = {
            kind: self._checked(ModuleSkeletonLowerer().lower(
                kind, design_ir, symbols.registry, files.registry
            )).manifest
            for kind in ("DB", "FUNC", "API")
        }
        report = json.loads(frontend_lowering_report_path.read_text(encoding="utf-8"))
        return CodeBindingLowerer().lower(
            output_root=self.output_root,
            requirement_ir=requirement_ir,
            dependency_graph=dependency_graph,
            design_ir=design_ir,
            frontend_ir=frontend_ir,
            backend_symbol_registry=symbols.registry,
            backend_type_manifest=types.manifest,
            backend_module_manifests=modules,
            backend_route_registry=backend_routes,
            frontend_symbol_registry={"symbols": []},
            frontend_file_registry={
                "ui_locations": [],
                "store_locations": [],
                "api_client_locations": [],
            },
            frontend_route_registry={"routes": []},
            frontend_lowering_report=report,
        )

    @staticmethod
    def _checked(result: Any) -> Any:
        if not result.ok:
            raise ValueError("; ".join(result.errors))
        return result


__all__ = ["CodeBindingPipeline"]
