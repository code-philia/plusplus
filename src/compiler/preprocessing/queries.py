"""Read-only requirement-tree queries; stages retain their scheduling policies."""
from typing import Any

from .traversal import children_by_id, postorder


def leaf_ids(requirements: dict[str, Any]) -> list[str]:
    """Return leaves in tree postorder, without applying dependency scheduling."""
    children = children_by_id(requirements)
    return [rid for rid in postorder(requirements) if not children[rid]]


def ancestor_ids(nodes: dict[str, Any], parent: str | None) -> list[str]:
    """Follow parents once each and return their IDs from root to nearest parent."""
    ancestors, seen = [], set()
    while parent in nodes and parent not in seen:
        seen.add(parent)
        ancestors.append(parent)
        parent = nodes[parent].get('parent_id')
    return list(reversed(ancestors))
