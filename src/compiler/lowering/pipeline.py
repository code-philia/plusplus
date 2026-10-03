from __future__ import annotations

from ..ir.design.module import DesignModule
from ..ir.implementation.project import ImplementationIR
from targets.react_fastapi import TargetProfile, load_target_profile
from .backend.passes import BackendLoweringPass
from .canonical import CanonicalDesignLowering
from .context import LoweringContext
from .database.passes import DatabaseLoweringPass
from .frontend.passes import FrontendLoweringPass


class LoweringPipeline:
    """Lower canonical Design IR through domain-specific lowering passes."""

    pass_pipeline = (DatabaseLoweringPass, BackendLoweringPass, FrontendLoweringPass)

    def __init__(self, target: str | TargetProfile = "react-fastapi") -> None:
        self.profile = (
            load_target_profile(target)
            if isinstance(target, str)
            else target
        )
        self.lowerer = CanonicalDesignLowering(self.profile)

    def lower(self, design: DesignModule) -> ImplementationIR:
        context = LoweringContext(
            design=design,
            implementation=self.lowerer.create_implementation(),
            lowerer=self.lowerer,
        )
        for pass_type in self.pass_pipeline:
            context = pass_type().run(context)
        self.lowerer.lower_edges(design, context.implementation)
        context.results["resolve_edges"] = {
            "status": "COMPLETED",
            "edge_count": len(context.implementation.edges),
        }
        return context.implementation


__all__ = ["LoweringPipeline"]
