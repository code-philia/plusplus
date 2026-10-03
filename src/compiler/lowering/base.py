from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .context import LoweringContext


@dataclass(frozen=True, slots=True)
class LoweringPassSpec:
    name: str
    input_level: str = "design"
    output_level: str = "implementation"


class LoweringPass:
    spec: LoweringPassSpec

    @property
    def input_level(self) -> str:
        return self.spec.input_level

    @property
    def output_level(self) -> str:
        return self.spec.output_level

    def run(self, context: LoweringContext) -> LoweringContext:
        context.results[self.spec.name] = {"status": "COMPLETED"}
        return context


__all__ = ["LoweringPass", "LoweringPassSpec"]
