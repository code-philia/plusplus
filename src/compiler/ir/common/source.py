from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceLocation:
    document: str
    pointer: str = "/"
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "pointer": self.pointer,
            "line": self.line,
            "column": self.column,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SourceLocation":
        return cls(
            document=str(value.get("document", "")),
            pointer=str(value.get("pointer", "/")),
            line=value.get("line"),
            column=value.get("column"),
        )


__all__ = ["SourceLocation"]