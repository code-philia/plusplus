"""Assign exported types to module files before rendering function skeletons."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..ir.backend import module_fields
from ..lowering.types import DESIGN_TYPE_MAP
from .skeletons import _relative_specifier


def attach_module_types(blueprint: dict[str, Any], design: dict[str, Any],
                        symbols: dict[str, Any], files: dict[str, Any], types: Any) -> None:
    """Move contracts out of shared, retaining registry identities for TDD."""
    modules = {row['id']: row for row in design['modules']}
    locations = {row['symbol_id']: row for row in files['symbol_locations']}
    file_rows = {row['path']: row for row in files['files']}
    definitions = {row['id']: row for row in blueprint['types']}
    type_rows = {row['symbol_id']: row for row in types.manifest['definitions']}
    contracts = [row for row in symbols['symbols'] if row['kind'] == 'DATA_CONTRACT']
    plans = {key: {'declarations': [], 'imports': [], 'parameter_types': {}, 'referenced_type_ids': []}
             for key in modules}
    module_paths = {key: locations[key]['path'] for key in modules}
    removed_paths: set[str] = set()

    def referenced(value: Any) -> set[str]:
        if isinstance(value, list):
            return set().union(*(referenced(item) for item in value)) if value else set()
        if not isinstance(value, dict):
            return set()
        result = {value['type_id']} if value.get('kind') == 'named' else set()
        for item in value.values():
            result |= referenced(item)
        return result

    consumers: dict[str, set[str]] = {key: set() for key in definitions}
    for key, module in modules.items():
        pending = list(referenced([module_fields(module['backend_module'], direction)
                                   for direction in ('inputs', 'outputs', 'context')]))
        seen: set[str] = set()
        while pending:
            type_id = pending.pop()
            if type_id in seen:
                continue
            seen.add(type_id)
            consumers[type_id].add(key)
            pending.extend(referenced(definitions[type_id]) - seen)
    if definitions and not modules:
        raise ValueError('Named backend types require a module to own their declarations')
    module_rank = lambda module_id: ({'DB': 0, 'FUNC': 1, 'API': 2}[modules[module_id]['kind']], module_id)
    owners = {key: min(values or modules.keys(), key=module_rank)
              for key, values in consumers.items()}
    used_names = {row['symbol'] for row in symbols['symbols']}
    names: dict[str, str] = {}
    for key in sorted(owners):
        name = ''.join(word[:1].upper() + word[1:] for word in re.findall(r'[A-Za-z0-9]+', key))
        if not name or name[0].isdigit():
            name = 'Type' + name
        if name in used_names:
            name += hashlib.sha256(key.encode()).hexdigest()[:8]
        used_names.add(name)
        names[key] = name
        for consumer in consumers[key]:
            plans[consumer]['referenced_type_ids'].append(key)
        path = module_paths[owners[key]]
        symbol = {'id': key, 'symbol': name, 'kind': 'NAMED_TYPE', 'typescript_kind': 'type',
                  'consumers': sorted(consumers[key]), 'owner_module': owners[key]}
        symbols['symbols'].append(symbol)
        files['symbol_locations'].append({'symbol_id': key, 'symbol': name,
                                          'typescript_kind': 'type', 'path': path})
        file_rows[path]['symbol_ids'].append(key)
        file_rows[path]['exports'].append(name)
        types.manifest['definitions'].append({'symbol_id': key, 'symbol': name,
                                              'kind': 'NAMED_TYPE', 'path': path})

    def add_import(owner: str, name: str, path: str) -> None:
        if path != module_paths[owner]:
            row = {'symbol': name, 'from': path, 'specifier': _relative_specifier(module_paths[owner], path),
                   'type_only': True}
            if row not in plans[owner]['imports']:
                plans[owner]['imports'].append(row)

    def render(value: dict[str, Any], owner: str) -> str:
        if value['kind'] == 'named':
            key = value['type_id']
            add_import(owner, names[key], module_paths[owners[key]])
            return names[key]
        if value['kind'] == 'array':
            return 'Array<' + render(value['items'], owner) + '>'
        result = DESIGN_TYPE_MAP[value['name']]
        if result == 'JsonValue':
            add_import(owner, result, 'shared/src/contracts/runtime.ts')
        return result

    def field_type(field: dict[str, Any], owner: str) -> str:
        return render(field['type'], owner) + (' | null' if field['nullable'] else '')

    def interface(name: str, rows: list[dict[str, Any]], owner: str) -> str:
        return '\n'.join([f'export interface {name} {{',
                          *(f'  {row["name"]}{"" if row["required"] else "?"}: {field_type(row, owner)};'
                            for row in rows), '}'])

    for key in sorted(owners):
        owner, definition = owners[key], definitions[key]
        declaration = (interface(names[key], definition['fields'], owner) if definition['kind'] == 'object'
                       else f'export type {names[key]} = ' + ' | '.join(json.dumps(value) for value in definition['values']) + ';')
        plans[owner]['declarations'].append(declaration)

    for contract in contracts:
        owner = min(contract['consumers'])
        suffix = {'INPUT': 'Input', 'OUTPUT': 'Output', 'CONTEXT': 'Context'}[contract['direction']]
        function_name = next(row['symbol'] for row in symbols['symbols'] if row['id'] == owner)
        preferred = function_name[:1].upper() + function_name[1:] + suffix
        if preferred in used_names:
            preferred += hashlib.sha256(contract['id'].encode()).hexdigest()[:8]
        used_names.add(preferred)
        contract['symbol'] = preferred
        location = locations[contract['id']]
        location['symbol'] = preferred
        type_rows[contract['id']]['symbol'] = preferred
        old_path, path = location['path'], module_paths[owner]
        removed_paths.add(old_path)
        location['path'] = path
        type_rows[contract['id']]['path'] = path
        file_rows[path]['symbol_ids'].append(contract['id'])
        file_rows[path]['exports'].append(contract['symbol'])
        direction = {'INPUT': 'inputs', 'OUTPUT': 'outputs', 'CONTEXT': 'context'}[contract['direction']]
        rows = module_fields(modules[owner]['backend_module'], direction)
        plans[owner]['declarations'].append(interface(contract['symbol'], rows, owner))

    for key, module in modules.items():
        for row in module_fields(module['backend_module'], 'inputs'):
            plans[key]['parameter_types'][row['name']] = field_type(row, key)
        module['type_plan'] = plans[key]

    files['files'] = [row for row in files['files'] if row['path'] not in removed_paths]
    for path in removed_paths:
        types.sources.pop(path, None)
    types.sources['shared/src/contracts/index.ts'] = 'export * from "./runtime.js";\n'
    types.manifest['generated_files'] = sorted(set(types.sources) | set(module_paths.values()))
