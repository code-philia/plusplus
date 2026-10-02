from __future__ import annotations

from dataclasses import dataclass, field

from .module import DesignNode
from ..common import NodeKind, Reference


@dataclass(slots=True)
class PageIR(DesignNode):
    route: str = "/"
    components: list[Reference] = field(default_factory=list)
    access: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.PAGE


@dataclass(slots=True)
class ComponentIR(DesignNode):
    props: list[str] = field(default_factory=list)
    children: list[Reference] = field(default_factory=list)
    events: list[Reference] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.COMPONENT


@dataclass(slots=True)
class UIIR(DesignNode):
    role: str = "container"
    text: str | None = None
    binds: list[Reference] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.UI


@dataclass(slots=True)
class EventIR(DesignNode):
    event_name: str = "click"
    handler: Reference | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.EVENT


@dataclass(slots=True)
class HandlerIR(DesignNode):
    invokes: list[Reference] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.HANDLER


@dataclass(slots=True)
class EffectIR(DesignNode):
    effect_type: str = "state"
    targets: list[Reference] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind = NodeKind.EFFECT



@dataclass(slots=True)
class FrontendDesignIR:
    """Typed view over the compiler-owned frontend tables."""

    tables: dict[str, dict[str, dict[str, object]]] = field(default_factory=dict)
    schema_version: int = 2

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "FrontendDesignIR":
        tables = {
            str(table): {
                str(row_id): dict(row)
                for row_id, row in rows.items()
                if isinstance(row, dict)
            }
            for table, rows in value.items()
            if isinstance(rows, dict)
        }
        return cls(tables=tables, schema_version=int(value.get("schema_version", 2)))

    def to_dict(self) -> dict[str, object]:
        return {table: {row_id: dict(row) for row_id, row in rows.items()} for table, rows in self.tables.items()}

    def rows(self, table: str) -> list[dict[str, object]]:
        return [dict(row) for row in self.tables.get(table, {}).values()]

    def get(self, table: str, row_id: str) -> dict[str, object] | None:
        row = self.tables.get(table, {}).get(row_id)
        return dict(row) if row is not None else None

    def requirements_for(self, row: dict[str, object]) -> list[str]:
        return list(row.get("requirement_ids", []) or [])


__all__ = ["ComponentIR", "EffectIR", "EventIR", "FrontendDesignIR", "HandlerIR", "PageIR", "UIIR"]