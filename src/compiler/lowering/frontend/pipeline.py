from __future__ import annotations

from pathlib import Path
from typing import Any

from .react import FrontendReactLowerer, ReactLoweringResult


class FrontendSourcePipeline:
    """Run React source lowering behind the frontend lowering boundary."""

    def __init__(self, project_root: Path) -> None:
        self.lowerer = FrontendReactLowerer(project_root)

    def run(
        self,
        *,
        frontend_ir: dict[str, Any],
        backend_design_ir: dict[str, Any],
        backend_routes: dict[str, Any],
        backend_port: int,
    ) -> ReactLoweringResult:
        return self.lowerer.lower(
            frontend_ir, backend_design_ir, backend_routes, backend_port
        )


__all__ = ["FrontendSourcePipeline"]
