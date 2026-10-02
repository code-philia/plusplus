from __future__ import annotations

from ..ir.design.module import DesignModule


def check_coverage(graph: DesignModule) -> list[str]:
    requirement_ids = {node.id for node in graph.nodes.values() if str(node.kind).lower() == "requirement"}
    covered = {edge.source_id for edge in graph.edges if edge.kind == "traces_to"}
    return [f"REQUIREMENT_UNCOVERED: {requirement_id}" for requirement_id in sorted(requirement_ids - covered)]


__all__ = ["check_coverage"]
