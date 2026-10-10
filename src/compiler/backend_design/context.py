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
              ('id', 'kind', 'inputs', 'context', 'outputs', 'entrypoint') if key in module}
    result['guarantees'] = [{key: row[key] for key in ('given', 'when', 'then')}
                            for row in module.get('acceptance_criteria', [])]
    return result


def refinement_context(module: dict[str, Any], modules: dict[str, Any],
                       schema: dict[str, Any]) -> dict[str, Any]:
    # Follow collaboration to identify affected data without exposing callee internals.
    pending, visited, accesses = [module['id']], set(), []
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        row = modules[key]
        accesses.extend(row['data_access'])
        pending.extend(call['module_id'] for call in row['calls'])
    selected: dict[str, set[str]] = {}
    for access in accesses:
        selected.setdefault(access['table'], set()).update(
            column for operation in ('read', 'create', 'update') for column in access[operation])
    result: dict[str, Any] = {}
    if module['kind'] == 'DB':
        tables = []
        for source in schema.get('tables', []):
            if source['name'] not in selected:
                continue
            columns = selected[source['name']]
            constraints = [row for row in source.get('constraints', [])
                           if columns & set(row.get('columns', []))
                           or any(a['table'] == source['name'] and a['delete'] for a in accesses)]
            needed = columns | {key for row in constraints for key in row.get('columns', [])}
            tables.append({**copy.deepcopy(source),
                           'columns': [copy.deepcopy(row) for row in source['columns'] if row['name'] in needed],
                           'constraints': copy.deepcopy(constraints)})
        result['tables'] = tables
    if module['kind'] == 'FUNC':
        # Runtime obligations belong to business orchestration, not HTTP forwarding.
        def relevant(rule: dict[str, Any]) -> bool:
            scope = rule.get('scope', '')
            table, _, column = scope.partition('.')
            mappings = rule.get('value', {})
            return (not scope and not mappings) or table in selected and (
                not column or column in selected[table]) or any(
                name in selected for name in _table_names(mappings))
        result['runtime_rules'] = [copy.deepcopy(row) for row in schema.get('runtime_rules', []) if relevant(row)]
    return result


def _table_names(value: Any) -> set[str]:
    if isinstance(value, list):
        return set().union(*(_table_names(item) for item in value)) if value else set()
    if not isinstance(value, dict):
        return set()
    own = {value[key] for key in ('table', 'owner_table', 'related_table') if isinstance(value.get(key), str)}
    return own | set().union(*(_table_names(item) for item in value.values()))
