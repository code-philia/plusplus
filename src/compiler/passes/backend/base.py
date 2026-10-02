from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class BackendPassSpec:
    name: str
    phase: str
    input_level: str = "requirement"
    output_level: str = "design"


@dataclass(slots=True)
class BackendPassContext:
    module: Any
    compiler: Any | None = None
    results: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


class BackendDesignPass:
    spec: BackendPassSpec

    def run(self, module: Any, context: BackendPassContext | None = None) -> BackendPassContext:
        context = context or BackendPassContext(module=module)
        context.module = module
        context.results[self.spec.name] = {"phase": self.spec.phase, "status": "COMPLETED"}
        return context


__all__ = ["BackendDesignPass", "BackendPassContext", "BackendPassSpec"]
