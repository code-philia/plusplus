from __future__ import annotations

from dataclasses import dataclass, field

from ..ir.design.module import DesignModule
from ..ir.implementation.project import ImplementationIR


@dataclass(slots=True)
class LoweringContext:
    design: DesignModule
    implementation: ImplementationIR = field(default_factory=ImplementationIR)
    diagnostics: list[str] = field(default_factory=list)


__all__ = ["LoweringContext"]
