from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceLocation:
    document: str
    pointer: str
    node_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"document": self.document, "pointer": self.pointer, "node_id": self.node_id}


@dataclass(frozen=True, slots=True)
class SourceMap:
    locations: dict[str, SourceLocation]

    @classmethod
    def from_requirement_ir(cls, requirement_ir: dict[str, Any]) -> "SourceMap":
        locations: dict[str, SourceLocation] = {}
        for node_id, node in (requirement_ir.get("nodes", {}) or {}).items():
            source = node.get("source", {}) if isinstance(node, dict) else {}
            locations[node_id] = SourceLocation(
                document=str(source.get("document", requirement_ir.get("source", {}).get("document", ""))),
                pointer=str(source.get("pointer", "")),
                node_id=node_id,
            )
        return cls(locations)

    def get(self, node_id: str) -> SourceLocation | None:
        return self.locations.get(node_id)

    def to_dict(self) -> dict[str, dict[str, str | None]]:
        return {node_id: location.as_dict() for node_id, location in sorted(self.locations.items())}


__all__ = ["SourceLocation", "SourceMap"]