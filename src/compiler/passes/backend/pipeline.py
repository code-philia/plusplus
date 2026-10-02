from __future__ import annotations

from .semantic import DesignPass, DesignPassResult, design_traceability
from ...ir.design.backend import BackendDesignIR
from .base import BackendPassContext
from .build_api import BuildAPIPass
from .decompose_api import DecomposeAPIPass
from .decompose_func import DecomposeFUNCPass
from .resolve_calls import ResolveCallsPass


class BackendIRGenerationPipeline(DesignPass):
    """New backend IR entry point preserving the existing design algorithm."""

    pass_pipeline = (BuildAPIPass, DecomposeAPIPass, DecomposeFUNCPass, ResolveCallsPass)

    def compile(self, requirement_ir, dependency_graph, database_schema) -> DesignPassResult:
        result = super().compile(requirement_ir, dependency_graph, database_schema)
        module = self.formalize(result.design_ir)
        context = BackendPassContext(module=module, compiler=self)
        for pass_type in self.pass_pipeline:
            context = pass_type().run(module, context)
        result.design_ir = module.to_dict()
        if not result.errors:
            result.warnings.extend(context.diagnostics)
        return result

    @staticmethod
    def formalize(design_ir: dict) -> BackendDesignIR:
        return BackendDesignIR.from_dict(design_ir)


__all__ = ["BackendDesignIR", "BackendIRGenerationPipeline", "design_traceability"]