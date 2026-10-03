from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..codegen import CodegenResult, DeterministicCodegen
from ..ir.design import DesignModule, build_design_graph
from ..ir.implementation import ImplementationIR
from ..lowering import LoweringPipeline
from ..passes.linking import LinkingPipeline
from ..validation.verifier import DesignIRVerifier, VerificationReport
from targets.react_fastapi import load_target_profile
from .compiler import ARCCompiler


@dataclass(slots=True)
class CanonicalPipelineResult:
    design_graph: DesignModule
    verification: VerificationReport
    implementation_ir: ImplementationIR
    codegen: CodegenResult


class CanonicalCompilerPipeline:
    """Run the canonical Design IR to codegen pipeline without side effects."""

    def __init__(self, *, target: str = "react-fastapi") -> None:
        profile = load_target_profile(target)
        self.lowering = LoweringPipeline(target=profile.name)
        self.codegen = DeterministicCodegen(profile)

    def run(
        self,
        *,
        database_schema: dict[str, Any],
        backend_ir: dict[str, Any],
        frontend_ir: dict[str, Any],
    ) -> CanonicalPipelineResult:
        design_graph = build_design_graph(
            database_schema=database_schema,
            backend_ir=backend_ir,
            frontend_ir=frontend_ir,
        )
        LinkingPipeline().run(design_graph)
        verification = DesignIRVerifier().verify(design_graph)
        implementation_ir = self.lowering.lower(design_graph)
        codegen = self.codegen.generate(implementation_ir)
        return CanonicalPipelineResult(
            design_graph=design_graph,
            verification=verification,
            implementation_ir=implementation_ir,
            codegen=codegen,
        )


def build_pipeline(*args, **kwargs) -> ARCCompiler:
    return ARCCompiler(*args, **kwargs)


__all__ = ["ARCCompiler", "CanonicalCompilerPipeline", "CanonicalPipelineResult", "build_pipeline"]
