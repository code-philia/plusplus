from __future__ import annotations

from ..ir.design.module import DesignModule
from ..ir.implementation.project import ImplementationIR
from .canonical import CanonicalDesignLowering


class LoweringPipeline:
    """Canonical Design IR to Implementation IR lowering entry point."""

    def __init__(self) -> None:
        self.backend = CanonicalDesignLowering()
        self.database = CanonicalDesignLowering()
        self.frontend = CanonicalDesignLowering()

    def lower(self, design: DesignModule) -> ImplementationIR:
        return self.backend.lower(design)


__all__ = ["LoweringPipeline"]
