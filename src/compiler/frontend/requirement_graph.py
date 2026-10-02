from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class RequirementGraph:
    nodes: dict[str, dict[str, Any]]
    root_id: str | None
    node_order: tuple[str, ...]

    @classmethod
    def from_ir(cls, requirement_ir: dict[str, Any]) -> "RequirementGraph":
        nodes = requirement_ir.get("nodes", {})
        return cls(
            nodes=dict(nodes) if isinstance(nodes, dict) else {},
            root_id=requirement_ir.get("root_id"),
            node_order=tuple(requirement_ir.get("node_order", ())),
        )

    def children(self, node_id: str) -> tuple[str, ...]:
        node = self.nodes.get(node_id, {})
        return tuple(node.get("children_ids", ()))

    def ancestors(self, node_id: str) -> tuple[str, ...]:
        result: list[str] = []
        current = self.nodes.get(node_id, {}).get("parent_id")
        while current:
            result.append(current)
            current = self.nodes.get(current, {}).get("parent_id")
        return tuple(result)

    def descendants(self, node_id: str) -> tuple[str, ...]:
        result: list[str] = []
        pending = list(self.children(node_id))
        while pending:
            current = pending.pop(0)
            result.append(current)
            pending[0:0] = list(self.children(current))
        return tuple(result)

    def walk(self) -> Iterable[dict[str, Any]]:
        for node_id in self.node_order:
            if node_id in self.nodes:
                yield self.nodes[node_id]


__all__ = ["RequirementGraph"]