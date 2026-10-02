from __future__ import annotations

from ...ir.design.contract import InvokeEdge
from .base import LinkingContext, LinkingPass


_REFERENCE_KEYS = ("api_id", "backend_api_id", "target_api_id", "api_ids", "backend_api_ids")


class LinkFrontendBackendPass(LinkingPass):
    name = "link_frontend_backend"

    def run(self, context: LinkingContext) -> LinkingContext:
        super().run(context)
        graph = context.graph
        api_ids = {node.id for node in graph.nodes.values() if str(node.kind).lower() == "api"}
        links: list[tuple[str, str]] = []
        for node in list(graph.nodes.values()):
            if str(node.kind).lower() not in {"event", "handler", "effect", "ui"}:
                continue
            for key in _REFERENCE_KEYS:
                value = node.attributes.get(key, [])
                values = value if isinstance(value, list) else [value]
                for target in values:
                    target_id = target.get("id") if isinstance(target, dict) else target
                    if target_id and str(target_id) in api_ids:
                        edge = InvokeEdge(node.id, str(target_id), {"source": key})
                        if edge not in graph.edges:
                            graph.add_edge(edge)
                            links.append((node.id, str(target_id)))
        context.results[self.name].update({"edge_count": len(links), "edges": links})
        return context


__all__ = ["LinkFrontendBackendPass"]
