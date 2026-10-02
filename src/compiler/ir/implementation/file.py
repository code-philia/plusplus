from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..common import IRLevel, IRNode, NodeKind, Reference


@dataclass(slots=True)
class FileIR(IRNode):
    path: str = ""
    language: str = ""
    imports: list[Reference] = field(default_factory=list)
    symbols: list[Reference] = field(default_factory=list)
    generated: bool = True

    def __post_init__(self) -> None:
        self.level = IRLevel.IMPLEMENTATION
        self.kind = NodeKind.FILE

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "path": self.path, "language": self.language, "generated": self.generated}


__all__ = ["FileIR"]