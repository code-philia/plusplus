from __future__ import annotations

from ..ir.design.module import DesignModule


def check_contracts(graph: DesignModule) -> list[str]:
    errors: list[str] = []
    for node in graph.nodes.values():
        if str(node.kind).lower() == "api" and not node.name:
            errors.append(f"API_NAME_MISSING: {node.id}")
        if str(node.kind).lower() == "function" and not node.name:
            errors.append(f"FUNCTION_NAME_MISSING: {node.id}")
    return errors


__all__ = ["check_contracts"]
