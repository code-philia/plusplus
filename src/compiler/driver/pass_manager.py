"""Minimal pass manager abstraction for incremental compiler migration."""

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass
class PassManager:
    passes: list[Callable[[Any], Any]] = field(default_factory=list)

    def add(self, compiler_pass: Callable[[Any], Any]) -> None:
        self.passes.append(compiler_pass)

    def run(self, value: Any) -> Any:
        for compiler_pass in self.passes:
            value = compiler_pass(value)
        return value


__all__ = ["PassManager"]
