from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from .reference import Reference
from .source import SourceLocation
from .types import IRLevel, JSONValue, NodeKind


@dataclass(slots=True)
class IRNode:
    id: str
    kind: NodeKind | str = NodeKind.REQUIREMENT
    level: IRLevel | str = IRLevel.REQUIREMENT
    name: str | None = None
    source: SourceLocation | None = None
    requirement_ids: list[str] = field(default_factory=list)
    attributes: dict[str, JSONValue] = field(default_factory=dict)

    node_kind: ClassVar[NodeKind | str] = NodeKind.REQUIREMENT

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": str(self.kind),
            "level": str(self.level),
            "name": self.name,
            "requirement_ids": list(self.requirement_ids),
            "attributes": dict(self.attributes),
        }
        if self.source:
            result["source"] = self.source.to_dict()
        return result

    @classmethod
    def _base_kwargs(cls, value: dict[str, Any], *, level: IRLevel | str, kind: NodeKind | str) -> dict[str, Any]:
        source = value.get("source")
        return {
            "id": str(value["id"]),
            "kind": value.get("kind", kind),
            "level": value.get("level", level),
            "name": value.get("name"),
            "source": SourceLocation.from_dict(source) if isinstance(source, dict) else None,
            "requirement_ids": list(value.get("requirement_ids", [])),
            "attributes": dict(value.get("attributes", {})),
        }


@dataclass(frozen=True, slots=True)
class IREdge:
    source_id: str
    target_id: str
    kind: str
    attributes: dict[str, JSONValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source_id, "target": self.target_id, "kind": self.kind, "attributes": dict(self.attributes)}


__all__ = ["IREdge", "IRNode"]