from __future__ import annotations

from dataclasses import dataclass, field

from ..ir.implementation.project import ImplementationIR


@dataclass(slots=True)
class CodegenResult:
    sources: dict[str, str] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"sources": dict(self.sources), "diagnostics": list(self.diagnostics)}


class DeterministicCodegen:
    """Generate stable source skeletons from Implementation IR only."""

    def __init__(self) -> None:
        from .python.generator import PythonCodegen
        from .typescript.generator import TypeScriptCodegen

        self._generators = {
            generator.language: generator
            for generator in (PythonCodegen(), TypeScriptCodegen())
        }

    def generate(self, implementation: ImplementationIR) -> CodegenResult:
        result = CodegenResult()
        for generator in self._generators.values():
            result.sources.update(generator.generate(implementation))
        return result


__all__ = ["CodegenResult", "DeterministicCodegen"]
