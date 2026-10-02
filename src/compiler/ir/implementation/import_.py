from __future__ import annotations

from dataclasses import dataclass

from ..common import IRLevel, IRNode, NodeKind


@dataclass(slots=True)
class ImportIR(IRNode):
    module: str = ""
    names: list[str] | None = None
    alias: str | None = None

    def __post_init__(self) -> None:
        self.level = IRLevel.IMPLEMENTATION
        self.kind = NodeKind.IMPORT


__all__ = ["ImportIR"]