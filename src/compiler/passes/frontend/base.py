from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FrontendPassSpec:
    name: str
    phase: str
    input_level: str = "requirement"
    output_level: str = "design"


@dataclass(slots=True)
class FrontendPassContext:
    compiler: Any
    tasks: list[dict[str, Any]] = field(default_factory=list)
    module: Any = None
    results: dict[str, Any] = field(default_factory=dict)


class FrontendDesignPass:
    spec: FrontendPassSpec

    @property
    def input_level(self) -> str:
        return self.spec.input_level

    @property
    def output_level(self) -> str:
        return self.spec.output_level

    def run(self, module: Any, context: FrontendPassContext) -> FrontendPassContext:
        context.module = module
        from .execution import run_frontend_data_phase, run_frontend_phase
        if self.spec.phase == "data":
            run_frontend_data_phase(context.compiler, context.tasks)
        else:
            run_frontend_phase(context.compiler, self.spec.phase, context.tasks)
        context.results[self.spec.name] = {
            "phase": self.spec.phase,
            "status": "COMPLETED",
        }
        return context


__all__ = ["FrontendDesignPass", "FrontendPassContext", "FrontendPassSpec"]
