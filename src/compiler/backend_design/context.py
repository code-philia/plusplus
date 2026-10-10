"""Deterministic model context projections; canonical Schema IR only."""
from __future__ import annotations

import copy
from typing import Any


def required_types(payload: Any, definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def names(value: Any) -> set[str]:
        if isinstance(value, list):
            return set().union(*(names(item) for item in value)) if value else set()
        if not isinstance(value, dict):
            return set()
        own = {value['type_id']} if value.get('kind') == 'named' else set()
        return own | set().union(*(names(item) for item in value.values()))

    registry = {row['id']: row for row in definitions}
    pending, found = list(names(payload)), set()
    while pending:
        key = pending.pop()
        if key not in found:
            found.add(key)
            pending.extend(names(registry.get(key, {})) - found)
    return [copy.deepcopy(row) for row in definitions if row['id'] in found]


def module_contract(module: dict[str, Any]) -> dict[str, Any]:
    """One behavior representation: guarantees instead of duplicate full spec."""
    result = {key: copy.deepcopy(module[key]) for key in
              ('id', 'kind', 'path', 'method', 'authentication', 'request', 'response', 'status_code',
               'content_type', 'parameters', 'returns', 'context', 'spec') if key in module}
    result['guarantees'] = [{key: row[key] for key in ('given', 'when', 'then')}
                            for row in module.get('acceptance_criteria', [])]
    return result
