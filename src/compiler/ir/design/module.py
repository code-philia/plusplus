from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..common import IREdge, IRLevel, IRNode, JSONValue, NodeKind, Reference


@dataclass(slots=True)
class DesignNode(IRNode):
    def __post_init__(self) -> None:
        self.level = IRLevel.DESIGN


@dataclass(slots=True)
class DesignModule:
    nodes: dict[str, DesignNode] = field(default_factory=dict)
    edges: list[IREdge] = field(default_factory=list)
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def add_node(self, node: DesignNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Duplicate design IR node: {node.id}")
        self.nodes[node.id] = node

    def add_edge(self, edge: IREdge) -> None:
        self.edges.append(edge)

    def get(self, node_id: str) -> DesignNode | None:
        return self.nodes.get(node_id)

    def outgoing(self, node_id: str, kind: str | None = None) -> list[IREdge]:
        return [edge for edge in self.edges if edge.source_id == node_id and (kind is None or edge.kind == kind)]

    def incoming(self, node_id: str, kind: str | None = None) -> list[IREdge]:
        return [edge for edge in self.edges if edge.target_id == node_id and (kind is None or edge.kind == kind)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
            "edges": [edge.to_dict() for edge in self.edges],
            "metadata": dict(self.metadata),
        }


DesignIR = DesignModule

__all__ = ["DesignIR", "DesignModule", "DesignNode"]