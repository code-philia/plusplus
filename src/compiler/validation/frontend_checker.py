from __future__ import annotations

from ..ir.design.module import DesignModule


def check_frontend(graph: DesignModule) -> list[str]:
    errors: list[str] = []
    frontend_kinds = {"page", "component", "ui", "event", "handler", "effect"}
    for edge in graph.edges:
        if edge.kind not in {"contains", "invokes", "data_flow"}:
            continue
        source = graph.nodes.get(edge.source_id)
        target = graph.nodes.get(edge.target_id)
        if source and str(source.kind).lower() in frontend_kinds and target is None:
            errors.append(f"FRONTEND_TARGET_MISSING: {edge.source_id}->{edge.target_id}")
    return errors


__all__ = ["check_frontend"]
