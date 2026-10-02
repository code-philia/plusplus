"""ARC compiler frontend: loading, canonicalization, graph construction, and source maps."""

from .canonicalize import RequirementCanonicalizer, RequirementShapeError, canonicalize_document, canonicalize_payload
from .loader import LoadedRequirementDocument, RequirementLoadError, RequirementLoader, load_requirement
from .requirement_graph import RequirementGraph
from .source_map import SourceLocation, SourceMap

__all__ = [
    "LoadedRequirementDocument",
    "RequirementCanonicalizer",
    "RequirementGraph",
    "RequirementLoadError",
    "RequirementLoader",
    "RequirementShapeError",
    "SourceLocation",
    "SourceMap",
    "canonicalize_document",
    "canonicalize_payload",
    "load_requirement",
]