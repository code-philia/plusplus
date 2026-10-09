"""Extension contract for database-specific lowering implementations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class StrategyResult:
    sources: dict[str, str]
    tables: list[dict[str, Any]]
    statements: list[str]
    policies: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


class DatabaseLoweringStrategy(Protocol):
    dialect: str

    def lower(self, schema: dict[str, Any], symbols: dict[str, Any], files: dict[str, Any]) -> StrategyResult: ...


def get_strategy(dialect: str) -> DatabaseLoweringStrategy:
    if dialect == 'sqlite':
        from .sqlite import SQLiteLoweringStrategy
        return SQLiteLoweringStrategy()
    raise ValueError(f'Unsupported database lowering strategy: {dialect!r}')
