from __future__ import annotations

from .semantic import FrontendIRGenerationPass, FrontendIRGenerationResult, frontend_ir_traceability
from ...ir.design.frontend import FrontendDesignIR
from .decompose_components import DecomposeComponentsPass
from .discover_pages import DiscoverPagesPass
from .infer_behavior import InferBehaviorPass
from .infer_data import InferDataPass


class FrontendIRGenerationPipeline(FrontendIRGenerationPass):
    """New frontend Design IR entry point preserving the existing workspace algorithm."""

    pass_pipeline = (DiscoverPagesPass, DecomposeComponentsPass, InferDataPass, InferBehaviorPass)

    @staticmethod
    def formalize(frontend_ir: dict) -> FrontendDesignIR:
        return FrontendDesignIR.from_dict(frontend_ir)


__all__ = ["FrontendDesignIR", "FrontendIRGenerationPipeline", "FrontendIRGenerationResult", "frontend_ir_traceability"]