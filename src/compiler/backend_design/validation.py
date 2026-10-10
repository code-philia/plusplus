"""Structural and graph checks; semantic completeness is reviewed separately."""
from __future__ import annotations

import re
from typing import Any

from ..ir.backend import module_fields

from .contracts import IR_SCHEMA, FIELD_NAME_PATTERN, SEMANTIC_ID_PATTERN


def shape_errors(value: Any, schema: dict[str, Any], path: str = '$', definitions: dict[str, Any] | None = None) -> list[str]:
    definitions = {**(definitions or {}), **schema.get('$defs', {})}
    if '$ref' in schema:
        return shape_errors(value, definitions[schema['$ref'].rsplit('/', 1)[1]], path, definitions)
    if 'anyOf' in schema:
        alternatives = [shape_errors(value, branch, path, definitions) for branch in schema['anyOf']]
        return [] if any(not errors for errors in alternatives) else min(alternatives, key=len)
    if 'enum' in schema and value not in schema['enum']:
        return [f'{path}: expected one of {schema["enum"]}']
    kind = schema.get('type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'boolean': isinstance(value, bool),
             'integer': isinstance(value, int) and not isinstance(value, bool),
             'number': isinstance(value, (int, float)) and not isinstance(value, bool),
             'null': value is None}
    if kind and not valid[kind]:
        return [f'{path}: expected {kind}']
    errors = []
    if kind == 'string' and 'pattern' in schema and not re.fullmatch(schema['pattern'], value):
        errors.append(f'{path}: must match {schema["pattern"]}; use snake_case names '
                      'such as image_source and lowercase semantic IDs such as home_banner.image_source')
    if kind == 'object':
        properties = schema.get('properties', {})
        errors += [f'{path}: missing {key}' for key in schema.get('required', []) if key not in value]
        for key, item in value.items():
            if key in properties:
                errors += shape_errors(item, properties[key], f'{path}.{key}', definitions)
            elif schema.get('additionalProperties') is False:
                errors.append(f'{path}: unexpected {key}')
            elif isinstance(schema.get('additionalProperties'), dict):
                errors += shape_errors(item, schema['additionalProperties'], f'{path}.{key}', definitions)
    if kind == 'array':
        for index, item in enumerate(value):
            errors += shape_errors(item, schema['items'], f'{path}[{index}]', definitions)
    return errors


def validate_data_access(access: list[dict[str, Any]], schema: dict[str, Any]) -> list[str]:
    tables = {table['name']: table for table in schema.get('tables', [])}
    errors = []
    seen = set()
    for row in access:
        table = row['table']
        if table in seen:
            errors.append(f'duplicate data_access table {table}')
        seen.add(table)
        if table not in tables:
            errors.append(f'unknown schema table {table}')
            continue
        columns = {column['name'] for column in tables[table]['columns']}
        for operation in ('read', 'create', 'update'):
            unknown = set(row[operation]) - columns
            if unknown:
                errors.append(f'{table}.{operation}: unknown columns {sorted(unknown)}')
            if len(row[operation]) != len(set(row[operation])):
                errors.append(f'{table}.{operation}: duplicate columns')
        if not any(row[key] for key in ('read', 'create', 'update', 'delete')):
            errors.append(f'{table}: empty direct data access')
    return errors


def access_set(rows: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    result = set()
    for row in rows:
        for operation in ('read', 'create', 'update'):
            result.update((row['table'], operation, column) for column in row[operation])
        if row['delete']:
            result.add((row['table'], 'delete', '*'))
    return result


def reachable(api_ids: list[str], modules: dict[str, dict[str, Any]]) -> set[str]:
    pending, seen = list(api_ids), set()
    while pending:
        key = pending.pop()
        if key in seen or key not in modules:
            continue
        seen.add(key)
        pending.extend(call['module_id'] for call in modules[key].get('calls', []))
    return seen


def validate_backend_ir(ir: dict[str, Any], schema: dict[str, Any], scenarios: set[str]) -> list[str]:
    errors = shape_errors(ir, IR_SCHEMA)
    if errors:
        return errors
    modules = {row['id']: row for row in ir['modules']}
    definitions = {row['id']: row for row in ir['types']}
    for label, rows, key in [('module', ir['modules'], 'id'), ('type', ir['types'], 'id')]:
        if len(rows) != len({row[key] for row in rows}):
            errors.append(f'duplicate {label} id')
    semantic_types: dict[str, Any] = {}

    def check_type(value: dict[str, Any], path: str, stack: tuple[str, ...] = ()) -> None:
        if value['kind'] == 'array':
            check_type(value['items'], path, stack)
        elif value['kind'] == 'named':
            key = value['type_id']
            if key not in definitions:
                errors.append(f'{path}: unknown named type {key}')
            elif key in stack:
                errors.append(f'{path}: recursive type {key}')
            elif definitions[key]['kind'] == 'object':
                for field in definitions[key]['fields']:
                    check_type(field['type'], path, (*stack, key))

    def check_fields(fields: list[dict[str, Any]], path: str) -> None:
        if len(fields) != len({field['name'] for field in fields}):
            errors.append(f'{path}: duplicate field name')
        if len(fields) != len({field['semantic_id'] for field in fields}):
            errors.append(f'{path}: duplicate semantic_id')
        for field in fields:
            if not re.fullmatch(FIELD_NAME_PATTERN, field['name']) or not re.fullmatch(SEMANTIC_ID_PATTERN, field['semantic_id']):
                errors.append(f'{path}: invalid field identity {field["name"]}/{field["semantic_id"]}; '
                              f'name must match {FIELD_NAME_PATTERN} (image_source), '
                              f'semantic_id must match {SEMANTIC_ID_PATTERN} (home_banner.image_source)')
            previous = semantic_types.setdefault(field['semantic_id'], field['type'])
            if previous != field['type']:
                errors.append(f'{path}: incompatible semantic type {field["semantic_id"]}')
            check_type(field['type'], path)

    def check_criteria(rows: list[dict[str, Any]], path: str) -> None:
        if not rows:
            errors.append(f'{path}: missing acceptance criteria')
        if len(rows) != len({row['id'] for row in rows}):
            errors.append(f'{path}: duplicate criterion id')
        for row in rows:
            if not all(row[key].strip() for key in ('id', 'given', 'when', 'then')):
                errors.append(f'{path}: criterion must have nonempty given/when/then')
            if set(row['scenario_ids']) - scenarios:
                errors.append(f'{path}: missing or unknown criterion scenarios')

    for definition in ir['types']:
        if not definition['id'].strip():
            errors.append('empty shared type id')
        if definition['kind'] == 'object':
            check_fields(definition['fields'], definition['id'])
        elif not definition['values'] or len(definition['values']) != len(set(definition['values'])):
            errors.append(f'{definition["id"]}: invalid enum values')
    allowed = {'API': {'FUNC'}, 'FUNC': {'FUNC', 'DB'}, 'DB': set()}
    for key, module in modules.items():
        if not re.fullmatch(r'.+::(API|FUNC|DB)\.[A-Za-z][A-Za-z0-9_]*', key) or f'::{module["kind"]}.' not in key:
            errors.append(f'invalid qualified module id {key}')
        if not module['spec'].strip():
            errors.append(f'{key}: empty spec')
        if not module['requirement_ids'] or any(not rid.strip() for rid in module['requirement_ids']):
            errors.append(f'{key}: unknown/empty requirement_ids')
        if len(module['requirement_ids']) != len(set(module['requirement_ids'])):
            errors.append(f'{key}: duplicate requirement association')
        if 'acceptance_criteria' in module:
            check_criteria(module['acceptance_criteria'], key)
        for direction in ('inputs', 'context', 'outputs'):
            check_fields(module_fields(module, direction), f'{key}.{direction}')
        if {row['name'] for row in module_fields(module, 'inputs')} & {row['name'] for row in module_fields(module, 'context')}:
            errors.append(f'{key}: input/context field names must be distinct')
        if {row['semantic_id'] for row in module_fields(module, 'inputs')} & {row['semantic_id'] for row in module_fields(module, 'context')}:
            errors.append(f'{key}: trusted context must not also be an untrusted input')
        if module['kind'] != 'DB' and module.get('data_access', []):
            errors.append(f'{key}: only DB leaves may access data directly')
        if module['kind'] == 'API':
            if not re.fullmatch(r'/(?:[a-zA-Z0-9_:/.-]*)', module['path']):
                errors.append(f'{key}: invalid REST path')
            if not 100 <= module['status_code'] <= 599:
                errors.append(f'{key}: invalid HTTP status code')
            if module['content_type'] != 'application/json':
                errors.append(f'{key}: response content_type must be application/json')
            path_fields = {field['name'] for field in module['request'] if field['source'] == 'path'}
            path_parameters = set(re.findall(r':([a-z][a-z0-9_]*)', module['path']))
            if path_fields != path_parameters:
                errors.append(f'{key}: path parameters differ from request fields')
            if module['method'] == 'GET' and any(field['source'] == 'body' for field in module['request']):
                errors.append(f'{key}: GET cannot have request body')
            if module['status_code'] in {204, 304} and module['response']:
                errors.append(f'{key}: no-body status cannot have response fields')
            if not module['response'] and module['status_code'] not in {204, 304}:
                errors.append(f'{key}: empty response requires no-body status')
        # Criteria may also originate from inherited parent rules. Global
        # scenario existence is checked above; provenance/composition is audited.
        if module['kind'] == 'DB' and not module.get('data_access', []):
            errors.append(f'{key}: DB leaf has no operation')
        errors += [f'{key}: {error}' for error in validate_data_access(module.get('data_access', []), schema)]
        call_ids = {call['id'] for call in module.get('calls', [])}
        if len(call_ids) != len(module.get('calls', [])):
            errors.append(f'{key}: duplicate call id')
        prior = set()
        for call in module.get('calls', []):
            target = modules.get(call['module_id'])
            if not call['id'].strip() or not call['purpose'].strip():
                errors.append(f'{key}: empty call identity/purpose')
            if target is None or target['kind'] not in allowed[module['kind']]:
                errors.append(f'{key}: illegal callee {call["module_id"]}')
            if set(call.get('after', [])) - prior:
                errors.append(f'{key}: after must refer to earlier local call IDs')
            if 'condition' in call and not call['condition'].strip():
                errors.append(f'{key}: empty call condition')
            prior.add(call['id'])
    visiting, finished = set(), set()

    def visit(key: str) -> None:
        if key in visiting:
            errors.append(f'call cycle at {key}')
            return
        if key in finished or key not in modules:
            return
        visiting.add(key)
        for call in modules[key].get('calls', []):
            visit(call['module_id'])
        visiting.remove(key)
        finished.add(key)

    for key in modules:
        visit(key)
    endpoints: set[tuple[str, str]] = set()
    for module in modules.values():
        if module['kind'] == 'API':
            endpoint = (module['method'], module['path'])
            if endpoint in endpoints:
                errors.append(f'duplicate REST endpoint {endpoint}')
            endpoints.add(endpoint)
    all_reachable = reachable([key for key, module in modules.items() if module['kind'] == 'API'], modules)
    if set(modules) - all_reachable:
        errors.append(f'unreachable modules: {sorted(set(modules) - all_reachable)}')
    return list(dict.fromkeys(errors))
