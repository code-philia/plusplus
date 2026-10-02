from __future__ import annotations

from ..ir.design.module import DesignModule


def check_database(graph: DesignModule) -> list[str]:
    errors: list[str] = []
    for edge in graph.edges:
        if edge.kind not in {"reads", "writes"}:
            continue
        target = graph.nodes.get(edge.target_id)
        if target is None or str(target.kind).lower() not in {"entity", "field"}:
            errors.append(f"DATABASE_TARGET_INVALID: {edge.source_id}->{edge.target_id}")
    return errors


__all__ = ["check_database"]
