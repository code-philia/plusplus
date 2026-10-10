"""Deterministic compatibility view for existing lowering/frontend consumers.

The backend blueprint remains authoritative; this view is never model generated.
"""
from __future__ import annotations
import copy
import json
from typing import Any


def project_lowering_design(ir: dict[str, Any]) -> dict[str, Any]:
    if ir.get('ir_kind') != 'backend_design':
        return ir
    definitions = {row['id']: row for row in ir['types']}
    modules = {row['id']: row for row in ir['modules']}

    def resolved_type(value: dict[str, Any]) -> dict[str, Any]:
        if value['kind'] == 'scalar':
            return value
        if value['kind'] == 'array':
            return {**value, 'items': resolved_type(value['items'])}
        definition = definitions[value['type_id']]
        if definition['kind'] == 'enum':
            return {'kind': 'enum', 'values': definition['values']}
        return {'kind': 'object', 'fields': [{**field, 'type': resolved_type(field['type'])} for field in definition['fields']]}

    def fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**copy.deepcopy(row), 'type': row['type'].get('name', 'json'),
                 'backend_type': resolved_type(row['type'])} for row in rows]

    def effects(key: str, active: set[str] | None = None) -> list[dict[str, Any]]:
        active = set(active or ())
        if key in active:
            raise ValueError(f'cyclic backend graph at {key}')
        active.add(key)
        module = modules[key]
        rows = []
        for access in module['data_access']:
            for operation in ('read', 'create', 'update'):
                if access[operation]:
                    rows.append({'id': f'{key}:{access["table"]}:{operation}', 'operation': operation.upper(),
                                 'target': access['table'], 'fields': access[operation]})
            if access['delete']:
                rows.append({'id': f'{key}:{access["table"]}:delete', 'operation': 'DELETE', 'target': access['table'], 'fields': []})
        for call in module['calls']:
            rows += effects(call['module_id'], active)
        return list({(row['operation'], row['target'], tuple(row['fields'])): row for row in rows}.values())

    callers: dict[str, list[str]] = {key: [] for key in modules}
    for key, module in modules.items():
        for call in module['calls']:
            callers[call['module_id']].append(key)
    projected = []
    for key, module in modules.items():
        # Retain full collaboration/criteria/context for implementation prompts,
        # including consumers that only pass the old prose spec downstream.
        blueprint = json.dumps({'context': module['context'], 'calls': module['calls'],
                                'data_access': module['data_access'], 'acceptance_criteria': module['acceptance_criteria']},
                               ensure_ascii=False, separators=(',', ':'))
        projected.append({**copy.deepcopy(module), 'owner_requirement': key.split('::', 1)[0],
                          # Separate context contracts keep trusted values out
                          # of HTTP input and ordinary input interfaces.
                          'inputs': fields(module['inputs']),
                          'context': fields(module['context']),
                          'backend_module': copy.deepcopy(module),
                          'outputs': fields(module['outputs']),
                          'spec': module['spec'] + '\nImplementation blueprint: ' + blueprint,
                          'effects': effects(key), 'callers': sorted(set(callers[key])),
                          'callees': list(dict.fromkeys(call['module_id'] for call in module['calls'])),
                          'obligations': [], 'self_obligation_ids': [], 'parent_id': None})
    projected_map = {row['id']: row for row in projected}
    requirements = []
    for row in ir['requirements']:
        inputs, outputs = {}, {}
        for key in row['api_ids']:
            for direction, destination in [('inputs', inputs), ('outputs', outputs)]:
                for value in projected_map[key][direction]:
                    destination[value['semantic_id']] = value
        requirements.append({**copy.deepcopy(row), 'id': row['requirement_id'],
                             'contract': {'spec': row['spec'], 'server_state': row['server_state'],
                                          'inputs': list(inputs.values()), 'outputs': list(outputs.values()),
                                          'effects': [], 'obligations': []}})
    return {'requirements': requirements, 'modules': projected, 'backend_blueprint': copy.deepcopy(ir)}
