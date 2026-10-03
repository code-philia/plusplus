from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .semantic import DesignIssue, DesignPassResult, DesignState


@dataclass(frozen=True, slots=True)
class BackendPassSpec:
    name: str
    phase: str
    input_level: str = "requirement"
    output_level: str = "design"


@dataclass(slots=True)
class BackendPassContext:
    compiler: Any
    module: Any = None
    requirement_id: str = ""
    requirement: dict[str, Any] = field(default_factory=dict)
    design_context: dict[str, Any] = field(default_factory=dict)
    state: DesignState | None = None
    contract: dict[str, Any] | None = None
    trial: DesignState | None = None
    api_ids: list[str] = field(default_factory=list)
    decision: dict[str, Any] | None = None
    upstream_feedback: list[str] = field(default_factory=list)
    seen_failures: set[str] = field(default_factory=set)
    last_trial: DesignState | None = None
    issues: list[DesignIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    node_states: dict[str, str] = field(default_factory=dict)
    retry: bool = False
    completed: bool = False
    result: DesignPassResult | None = None
    results: dict[str, Any] = field(default_factory=dict)


class BackendDesignPass:
    spec: BackendPassSpec

    @property
    def input_level(self) -> str:
        return self.spec.input_level

    @property
    def output_level(self) -> str:
        return self.spec.output_level

    def run(self, module: Any, context: BackendPassContext | None = None) -> BackendPassContext:
        if context is None:
            raise ValueError("Backend passes require a transaction context")
        context.module = module
        context.results[self.spec.name] = {"phase": self.spec.phase, "status": "COMPLETED"}
        return context


__all__ = ["BackendDesignPass", "BackendPassContext", "BackendPassSpec"]
