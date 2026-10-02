from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Reference:
    """Typed edge endpoint; IDs are stable within one IR module."""

    target_id: str
    role: str | None = None

    def to_dict(self) -> dict[str, str]:
        result = {"id": self.target_id}
        if self.role:
            result["role"] = self.role
        return result

    @classmethod
    def from_value(cls, value: Any) -> "Reference":
        if isinstance(value, str):
            return cls(value)
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            return cls(value["id"], value.get("role"))
        raise ValueError(f"Invalid IR reference: {value!r}")


__all__ = ["Reference"]