from __future__ import annotations

from ...ir.common import IRNode, NodeKind
from ...ir.design.contract import RequirementTrace
from .base import LinkingContext, LinkingPass


class LinkRequirementsPass(LinkingPass):
    name = "link_requirements"

    def run(self, context: LinkingContext) -> LinkingContext:
        super().run(context)
        graph = context.graph
        linked: list[tuple[str, str]] = []
        for node in list(graph.nodes.values()):
            for requirement_id in node.requirement_ids:
                if requirement_id not in graph.nodes:
                    graph.add_node(IRNode(id=requirement_id, kind=NodeKind.REQUIREMENT, name=requirement_id))
                edge = RequirementTrace(requirement_id, node.id)
                if edge not in graph.edges:
                    graph.add_edge(edge)
                    linked.append((requirement_id, node.id))
        context.results[self.name].update({"edge_count": len(linked), "edges": linked})
        return context


__all__ = ["LinkRequirementsPass"]
