from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..common import IRLevel, IRNode, NodeKind


@dataclass(slots=True)
class TypeIR(IRNode):
    name: str = "Any"
    nullable: bool = False
    generic_arguments: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.level = IRLevel.IMPLEMENTATION
        self.kind = NodeKind.TYPE

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "type_name": self.name, "nullable": self.nullable, "generic_arguments": list(self.generic_arguments)}


__all__ = ["TypeIR"]