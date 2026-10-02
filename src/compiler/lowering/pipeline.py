from __future__ import annotations

from ..ir.design.module import DesignModule
from ..ir.implementation.project import ImplementationIR
from .canonical import CanonicalDesignLowering
from targets.react_fastapi import load_target_profile


class LoweringPipeline:
    """Canonical Design IR to Implementation IR lowering entry point."""

    def __init__(self, target: str = "react-fastapi") -> None:
        profile = load_target_profile(target)
        self.backend = CanonicalDesignLowering(profile)
        self.database = CanonicalDesignLowering(profile)
        self.frontend = CanonicalDesignLowering(profile)

    def lower(self, design: DesignModule) -> ImplementationIR:
        return self.backend.lower(design)


__all__ = ["LoweringPipeline"]
