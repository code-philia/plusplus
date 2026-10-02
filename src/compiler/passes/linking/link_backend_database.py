from __future__ import annotations

from ...ir.design.contract import ReadEdge, WriteEdge
from .base import LinkingContext, LinkingPass


class LinkBackendDatabasePass(LinkingPass):
    name = "link_backend_database"

    def run(self, context: LinkingContext) -> LinkingContext:
        super().run(context)
        graph = context.graph
        entity_ids = {node.id for node in graph.nodes.values() if str(node.kind).lower() == "entity"}
        links: list[tuple[str, str]] = []
        for node in list(graph.nodes.values()):
            if str(node.kind).lower() not in {"database_operation", "function", "api"}:
                continue
            targets = node.attributes.get("entity") or node.attributes.get("target")
            values = targets if isinstance(targets, list) else [targets]
            for target in values:
                target_id = target.get("id") if isinstance(target, dict) else target
                if not target_id or str(target_id) not in entity_ids:
                    continue
                operation = str(node.attributes.get("operation", "read")).upper()
                edge_type = WriteEdge if operation in {"CREATE", "UPDATE", "DELETE", "WRITE"} else ReadEdge
                edge = edge_type(node.id, str(target_id), {"operation": operation})
                if edge not in graph.edges:
                    graph.add_edge(edge)
                    links.append((node.id, str(target_id)))
        context.results[self.name].update({"edge_count": len(links), "edges": links})
        return context


__all__ = ["LinkBackendDatabasePass"]
