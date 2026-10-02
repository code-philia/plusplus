from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .file import FileIR
from .symbol import SymbolIR
from ..common import IREdge, JSONValue


@dataclass(slots=True)
class ImplementationIR:
    files: dict[str, FileIR] = field(default_factory=dict)
    symbols: dict[str, SymbolIR] = field(default_factory=dict)
    types: dict[str, Any] = field(default_factory=dict)
    functions: dict[str, Any] = field(default_factory=dict)
    components: dict[str, Any] = field(default_factory=dict)
    imports: dict[str, Any] = field(default_factory=dict)
    edges: list[IREdge] = field(default_factory=list)
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def serialize(value: Any) -> Any:
            return value.to_dict() if hasattr(value, "to_dict") else value
        return {
            "schema_version": 1,
            "files": {key: serialize(value) for key, value in self.files.items()},
            "symbols": {key: serialize(value) for key, value in self.symbols.items()},
            "types": {key: serialize(value) for key, value in self.types.items()},
            "functions": {key: serialize(value) for key, value in self.functions.items()},
            "components": {key: serialize(value) for key, value in self.components.items()},
            "imports": {key: serialize(value) for key, value in self.imports.items()},
            "edges": [edge.to_dict() for edge in self.edges],
            "metadata": dict(self.metadata),
        }


__all__ = ["ImplementationIR"]