from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import JSONValue


@dataclass(frozen=True, slots=True)
class IRValue:
    value: JSONValue

    def to_dict(self) -> JSONValue:
        return self.value

    @classmethod
    def from_value(cls, value: Any) -> "IRValue":
        return cls(value)


__all__ = ["IRValue"]