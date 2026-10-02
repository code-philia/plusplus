from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..common import IRLevel, IRNode, NodeKind


@dataclass(slots=True)
class RequirementNode(IRNode):
    type: str = "ATOMIC"
    description: str = ""
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    visual_references: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.kind = NodeKind.REQUIREMENT
        self.level = IRLevel.REQUIREMENT

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "type": self.type,
            "description": self.description,
            "parent_id": self.parent_id,
            "children_ids": list(self.children_ids),
            "dependencies": list(self.dependencies),
            "scenarios": list(self.scenarios),
            "visual_references": list(self.visual_references),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RequirementNode":
        base = cls._base_kwargs(value, level=IRLevel.REQUIREMENT, kind=NodeKind.REQUIREMENT)
        return cls(
            **base,
            type=str(value.get("type", "ATOMIC")),
            description=str(value.get("description", "")),
            parent_id=value.get("parent_id"),
            children_ids=list(value.get("children_ids", [])),
            dependencies=list(value.get("dependencies", [])),
            scenarios=list(value.get("scenarios", [])),
            visual_references=list(value.get("visual_references", [])),
        )


@dataclass(slots=True)
class RequirementIR:
    schema_version: int = 1
    source: dict[str, Any] = field(default_factory=dict)
    root_id: str | None = None
    node_order: list[str] = field(default_factory=list)
    nodes: dict[str, RequirementNode] = field(default_factory=dict)

    @property
    def atomic_units(self) -> list[str]:
        return [node_id for node_id in self.node_order if self.nodes.get(node_id, None) and self.nodes[node_id].type == "ATOMIC"]

    @property
    def folder_nodes(self) -> list[str]:
        return [node_id for node_id in self.node_order if self.nodes.get(node_id, None) and self.nodes[node_id].type == "FOLDER"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": dict(self.source),
            "root_id": self.root_id,
            "node_order": list(self.node_order),
            "atomic_units": self.atomic_units,
            "folder_nodes": self.folder_nodes,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RequirementIR":
        raw_nodes = value.get("nodes", {})
        nodes = {
            node_id: RequirementNode.from_dict({**node, "id": node.get("id", node_id)})
            for node_id, node in raw_nodes.items()
            if isinstance(node, dict)
        }
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            source=dict(value.get("source", {})),
            root_id=value.get("root_id"),
            node_order=list(value.get("node_order", nodes.keys())),
            nodes=nodes,
        )


__all__ = ["RequirementIR", "RequirementNode"]