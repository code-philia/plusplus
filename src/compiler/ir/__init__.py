"""ARC's three-level intermediate representation."""

from .common import IREdge, IRLevel, IRNode, IRValue, JSONScalar, JSONValue, NodeKind, Reference, SourceLocation
from .design import BackendDesignIR, DesignIR, DesignModule, DesignNode
from .implementation import ImplementationIR
from .requirement import RequirementIR, RequirementNode

__all__ = [
    "DesignIR", "DesignModule", "DesignNode", "IREdge", "IRLevel", "IRNode", "IRValue", "ImplementationIR",
    "JSONScalar", "JSONValue", "NodeKind", "Reference", "RequirementIR", "RequirementNode", "SourceLocation",
]