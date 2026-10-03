from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..ir.design.module import DesignModule
from ..ir.implementation.project import ImplementationIR
from .canonical import CanonicalDesignLowering


@dataclass(slots=True)
class LoweringContext:
    design: DesignModule
    implementation: ImplementationIR = field(default_factory=ImplementationIR)
    lowerer: CanonicalDesignLowering | None = None
    diagnostics: list[str] = field(default_factory=list)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)


__all__ = ["LoweringContext"]
