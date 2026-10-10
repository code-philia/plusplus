"""Requirement-tree traversal; dependency edges are context, never visit order."""
from typing import Any


def children_by_id(requirement_ir: dict[str, Any]) -> dict[str, list[str]]:
    nodes = requirement_ir.get('nodes', {})
    order = list(dict.fromkeys([*requirement_ir.get('node_order', []), *nodes]))
    children = {key: list(dict.fromkeys(row.get('children_ids', []))) for key, row in nodes.items()}
    for key in order:
        if key not in nodes:
            raise ValueError(f'Missing requirement node {key}')
        parent = nodes[key].get('parent_id')
        if parent in nodes and key not in children[parent]:
            children[parent].append(key)
    return children


def postorder(requirement_ir: dict[str, Any]) -> list[str]:
    """Visit each node once, children before parent, preserving sibling order."""
    nodes = requirement_ir.get('nodes', {})
    children = children_by_id(requirement_ir)
    result, visited, active = [], set(), set()

    def visit(key: str) -> None:
        if key in active:
            raise ValueError(f'Requirement tree cycle at {key}')
        if key in visited:
            return
        if key not in nodes:
            raise ValueError(f'Missing requirement node {key}')
        active.add(key)
        for child in children[key]:
            visit(child)
        active.remove(key)
        visited.add(key)
        result.append(key)

    root = requirement_ir.get('root_id')
    if root not in nodes:
        raise ValueError(f'Missing requirement root {root}')
    visit(root)
    if missing := set(nodes) - visited:
        raise ValueError(f'Requirements unreachable from root: {sorted(missing)}')
    return result
