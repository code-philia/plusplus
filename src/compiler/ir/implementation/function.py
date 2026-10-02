from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..common import IRLevel, IRNode, NodeKind, Reference


@dataclass(slots=True)
class FunctionIR(IRNode):
    parameters: list[dict[str, Any]] = field(default_factory=list)
    return_type: str = "None"
    body: list[str] = field(default_factory=list)
    calls: list[Reference] = field(default_factory=list)
    async_: bool = False

    def __post_init__(self) -> None:
        self.level = IRLevel.IMPLEMENTATION
        self.kind = NodeKind.FUNCTION


__all__ = ["FunctionIR"]