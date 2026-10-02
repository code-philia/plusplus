from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..common import IRLevel, IRNode, NodeKind, Reference


@dataclass(slots=True)
class ComponentIR(IRNode):
    framework: str = "react"
    props: list[dict[str, Any]] = field(default_factory=list)
    children: list[Reference] = field(default_factory=list)
    events: list[Reference] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.level = IRLevel.IMPLEMENTATION
        self.kind = NodeKind.COMPONENT


__all__ = ["ComponentIR"]