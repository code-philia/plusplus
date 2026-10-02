from __future__ import annotations

from ..ir.design.module import DesignModule


def check_references(graph: DesignModule) -> list[str]:
    errors: list[str] = []
    for edge in graph.edges:
        if edge.source_id not in graph.nodes:
            errors.append(f"REFERENCE_SOURCE_MISSING: {edge.source_id}")
        if edge.target_id not in graph.nodes:
            errors.append(f"REFERENCE_TARGET_MISSING: {edge.target_id}")
    return errors


__all__ = ["check_references"]
