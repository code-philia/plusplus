from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..preprocessing_stage import PreprocessingResult, RequirementPreprocessor
from .canonicalize import RequirementShapeError, canonicalize_payload
from .loader import RequirementLoadError, RequirementLoader
from .requirement_graph import RequirementGraph
from .source_map import SourceMap


@dataclass(frozen=True, slots=True)
class FrontendResult:
    preprocessing: PreprocessingResult
    graph: RequirementGraph | None = None
    source_map: SourceMap | None = None

    @property
    def ok(self) -> bool:
        return self.preprocessing.ok


class RequirementFrontend:
    """Load and lower external requirement documents into ARC Requirement IR."""

    def __init__(self, preprocessor: RequirementPreprocessor | None = None) -> None:
        self.loader = RequirementLoader()
        self.preprocessor = preprocessor or RequirementPreprocessor()

    def compile(self, source: str | Path) -> FrontendResult:
        try:
            document = self.loader.load(source)
            canonicalize_payload(document.payload)
        except (RequirementLoadError, RequirementShapeError) as exc:
            result = PreprocessingResult({}, {}, [f"ARC1001: {exc}"])
            return FrontendResult(result)
        result = self.preprocessor.compile(document.path)
        graph = RequirementGraph.from_ir(result.requirement_ir) if result.requirement_ir else None
        source_map = SourceMap.from_requirement_ir(result.requirement_ir) if result.requirement_ir else None
        return FrontendResult(result, graph, source_map)


__all__ = ["FrontendResult", "RequirementFrontend"]