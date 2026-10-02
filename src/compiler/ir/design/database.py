from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .module import DesignNode
from ..common import IRLevel, NodeKind, Reference


@dataclass(slots=True)
class FieldIR(DesignNode):
    type_name: str = "string"
    required: bool = False
    unique: bool = False
    default: Any = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.FIELD


@dataclass(slots=True)
class EntityIR(DesignNode):
    fields: list[Reference] = field(default_factory=list)
    primary_key: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.ENTITY


@dataclass(slots=True)
class RelationIR(DesignNode):
    source_entity: Reference = field(default_factory=lambda: Reference(""))
    target_entity: Reference = field(default_factory=lambda: Reference(""))
    relation_type: str = "references"

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.RELATION


__all__ = ["EntityIR", "FieldIR", "RelationIR"]