from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..common import IRLevel, IRNode, NodeKind, Reference


@dataclass(slots=True)
class SymbolIR(IRNode):
    symbol_kind: str = "value"
    type_ref: Reference | None = None
    exported: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.level = IRLevel.IMPLEMENTATION
        self.kind = NodeKind.SYMBOL


__all__ = ["SymbolIR"]