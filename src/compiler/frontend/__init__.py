"""ARC compiler frontend: loading, canonicalization, graph construction, and source maps."""

from .canonicalize import RequirementCanonicalizer, RequirementShapeError, canonicalize_document, canonicalize_payload
from .context import component_requirement_owners, frontend_subgraph, implementation_requirement, visual_context
from .loader import LoadedRequirementDocument, RequirementLoadError, RequirementLoader, load_requirement
from .preprocessing import PreprocessingResult, RequirementPreprocessor
from .requirement_graph import RequirementGraph
from .source_map import SourceLocation, SourceMap

__all__ = [
    "LoadedRequirementDocument",
    "PreprocessingResult",
    "RequirementCanonicalizer",
    "RequirementPreprocessor",
    "RequirementGraph",
    "RequirementLoadError",
    "RequirementLoader",
    "RequirementShapeError",
    "SourceLocation",
    "SourceMap",
    "canonicalize_document",
    "canonicalize_payload",
    "component_requirement_owners",
    "frontend_subgraph",
    "implementation_requirement",
    "load_requirement",
    "visual_context",
]
