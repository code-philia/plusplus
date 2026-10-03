from __future__ import annotations

from typing import Protocol

from ..ir.implementation.project import ImplementationIR


class LanguageCodegen(Protocol):
    language: str

    def generate(self, implementation: ImplementationIR) -> dict[str, str]:
        ...


__all__ = ["LanguageCodegen"]
