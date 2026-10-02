from __future__ import annotations

from copy import deepcopy
from typing import Any

from .loader import LoadedRequirementDocument


class RequirementShapeError(ValueError):
    """Raised when a loaded document cannot be converted to a requirement tree."""


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("id") or node.get("req_id") or "").strip()


def _expand_flat_requirements(items: list[Any], root_id: str | None = None) -> dict[str, Any]:
    records = { _node_id(item): deepcopy(item) for item in items if isinstance(item, dict) and _node_id(item) }
    if not records:
        raise RequirementShapeError("requirements must contain at least one node with an id")
    referenced = {
        str(child).strip()
        for record in records.values()
        for child in (record.get("children") or [])
        if isinstance(child, str) and str(child).strip()
    }
    selected_root = root_id or next((key for key in records if key not in referenced), None)
    if selected_root is None:
        raise RequirementShapeError("requirements contain no root node")
    visiting: set[str] = set()

    def expand(node_id: str) -> dict[str, Any]:
        if node_id in visiting:
            raise RequirementShapeError(f"Requirement tree cycle at {node_id}")
        record = deepcopy(records.get(node_id, {"id": node_id}))
        children = record.get("children") or []
        visiting.add(node_id)
        expanded: list[dict[str, Any]] = []
        for child in children:
            child_id = child if isinstance(child, str) else _node_id(child) if isinstance(child, dict) else ""
            if child_id and child_id in records:
                expanded.append(expand(child_id))
            elif isinstance(child, dict):
                expanded.append(child)
        visiting.remove(node_id)
        record["children"] = expanded
        if expanded and not record.get("type"):
            record["type"] = "FOLDER"
        return record

    return expand(selected_root)


def canonicalize_payload(payload: Any) -> dict[str, Any]:
    """Convert supported source shapes into one nested requirement-tree root."""
    if not isinstance(payload, dict):
        raise RequirementShapeError("Requirement document root must be a mapping")
    if isinstance(payload.get("root"), dict):
        return deepcopy(payload["root"])
    if isinstance(payload.get("requirement"), dict):
        return deepcopy(payload["requirement"])
    if isinstance(payload.get("requirements"), list):
        return _expand_flat_requirements(payload["requirements"], payload.get("root_id"))
    if _node_id(payload):
        return deepcopy(payload)
    raise RequirementShapeError("Requirement document must contain root, requirement, requirements, or id")


def canonicalize_document(document: LoadedRequirementDocument) -> dict[str, Any]:
    return canonicalize_payload(document.payload)


class RequirementCanonicalizer:
    def canonicalize(self, document: LoadedRequirementDocument | Any) -> dict[str, Any]:
        if isinstance(document, LoadedRequirementDocument):
            return canonicalize_document(document)
        return canonicalize_payload(document)


__all__ = ["RequirementCanonicalizer", "RequirementShapeError", "canonicalize_document", "canonicalize_payload"]