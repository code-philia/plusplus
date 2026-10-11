"""Deterministic model context projections; canonical Schema IR only."""
from __future__ import annotations

import copy
import json
import re
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


def reuse_context(ir: dict[str, Any], modules: list[dict[str, Any]], query: Any,
                  requested_type_ids: set[str] | None = None) -> dict[str, Any]:
    """Expose loaded contracts and discoverable IDs before inventing new contracts."""
    terms = lambda value: set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]',
                                        json.dumps(value, ensure_ascii=False).lower()))
    wanted = terms(query)
    ranked = sorted(ir['types'], key=lambda row: (-len(wanted & terms(row)), row['id']))[:12]
    seeds = [*modules, *[{'kind': 'named', 'type_id': row['id']} for row in ranked],
             *[{'kind': 'named', 'type_id': key} for key in sorted(requested_type_ids or set())]]
    return {
        'modules': copy.deepcopy(modules),
        'types': required_types(seeds, ir['types']),
        'module_catalog': [{key: copy.deepcopy(row[key]) for key in ('id', 'kind', 'path', 'method') if key in row}
                           | {'spec': row['spec'][:240]} for row in ir['modules']],
        'type_catalog': [{'id': row['id'], 'kind': row['kind'],
                         'semantic_ids': [field['semantic_id'] for field in row.get('fields', [])]}
                        for row in ir['types']],
        'reuse_policy': 'Reference loaded records by ID; design only missing responsibilities.',
    }


def requirement_contracts(ir: dict[str, Any], requirement_ids: list[str]) -> dict[str, Any]:
    """Derived summaries only: module contracts remain the authoritative records."""
    result = {}
    for rid in requirement_ids:
        modules = [row for row in ir['modules'] if rid in row['requirement_ids']]
        operations: dict[str, dict[str, Any]] = {}
        for module in modules:
            for access in module.get('data_access', []):
                merged = operations.setdefault(access['table'], {'table': access['table'],
                    'read': set(), 'create': set(), 'update': set(), 'delete': False})
                for key in ('read', 'create', 'update'):
                    merged[key].update(access[key])
                merged['delete'] |= access['delete']
        result[rid] = {
            'module_ids': sorted(row['id'] for row in modules),
            'api_ids': sorted(row['id'] for row in modules if row['kind'] == 'API'),
            'type_ids': sorted(row['id'] for row in required_types(modules, ir['types'])),
            'data_access': [{key: sorted(value) if isinstance(value, set) else value
                             for key, value in operations[table].items()} for table in sorted(operations)],
        }
    return {'derived_from': 'backend_ir.json', 'requirements': result}
