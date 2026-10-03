from __future__ import annotations

from .semantic import DesignPass, DesignPassResult, design_traceability
from ...ir.design.backend import BackendDesignIR
from .build_api import BuildAPIPass
from .decompose_api import DecomposeAPIPass
from .decompose_func import DecomposeFUNCPass
from .resolve_calls import ResolveCallsPass


class BackendIRGenerationPipeline(DesignPass):
    """Backend IR entry point executed through explicit semantic passes."""

    pass_pipeline = (BuildAPIPass, DecomposeAPIPass, DecomposeFUNCPass, ResolveCallsPass)

    def compile(self, requirement_ir, dependency_graph, database_schema) -> DesignPassResult:
        result = self.compile_staged(requirement_ir, dependency_graph, database_schema)
        return result

    @staticmethod
    def formalize(design_ir: dict) -> BackendDesignIR:
        return BackendDesignIR.from_dict(design_ir)


__all__ = ["BackendDesignIR", "BackendIRGenerationPipeline", "design_traceability"]