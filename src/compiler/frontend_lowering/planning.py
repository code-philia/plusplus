"""Deterministic React file boundaries derived from the semantic UI tree."""
from __future__ import annotations

from compiler.ir.frontend import frontend_events

import hashlib
import re
from collections import Counter
from typing import Any


def _pascal(value: str) -> str:
    words = re.findall(r'[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|[0-9]+', value)
    result = ''.join(word[:1].upper() + word[1:] for word in words)
    return ('Ui' + result) if not result or result[0].isdigit() else result


def _semantic_name(row: dict[str, Any], root: str) -> str:
    if row['id'] == root:
        return 'ApplicationShell'
    value = str(row['name']).strip()
    if not value:
        raise ValueError(f"UI requires a semantic name: {row['id']}")
    name = _pascal(value)
    if row['kind'] == 'screen' and not name.endswith('Page'):
        name += 'Page'
    if name in {'React', 'UIBoundary', 'UIProps'}:
        name += 'View'
    return name


def plan_ui_units(ir: dict[str, Any]) -> dict[str, Any]:
    ui = {row['id']: row for row in ir['ui']}
    root = ir['root_ui_id']
    names = {key: _semantic_name(row, root) for key, row in ui.items()}
    base_names = dict(names)
    parents = {child: key for key, row in ui.items() for child in row['children']}

    def allocate(keys: list[str], reserved: set[str]) -> None:
        counts = Counter(base_names[key].casefold() for key in keys)
        occupied = {name.casefold() for name in reserved}
        for key in sorted(keys):
            name = base_names[key]
            if counts[name.casefold()] > 1 or name.casefold() in occupied:
                parent = base_names.get(parents.get(key, ''), 'Shared')
                name = parent + name
            if name.casefold() in occupied:
                name += hashlib.sha256(key.encode()).hexdigest()[:6]
            candidate, suffix = name, 2
            while candidate.casefold() in occupied:
                candidate = name + str(suffix)
                suffix += 1
            names[key] = candidate
            occupied.add(candidate.casefold())
    boundaries = {root} | {key for key, row in ui.items()
                           if row['kind'] == 'screen' or row.get('repeat')}

    def contains_screen(key: str, seen: set[str]) -> bool:
        if key in seen:
            return False
        seen.add(key)
        row = ui.get(key, {})
        return row.get('kind') == 'screen' or any(
            contains_screen(child, seen) for child in row.get('children', []))

    # Header/footer and other shell regions get a boundary. A main outlet
    # containing screens stays in the shell, rather than creating an extra file.
    boundaries.update(key for key in ui[root]['children']
                      if ui[key]['kind'] == 'group' and not contains_screen(key, set()))
    units: dict[str, Any] = {}
    membership: dict[str, str] = {}

    def visit(key: str, owner: str, active: set[str]) -> None:
        if key in active:
            raise ValueError(f'UI tree cycle at {key}')
        if key in membership:
            raise ValueError(f'UI node has multiple parents: {key}')
        row = ui[key]
        if key in boundaries:
            owner = key
        if owner not in units:
            folder = 'pages' if ui[owner]['kind'] == 'screen' else 'components'
            units[owner] = {'id': owner, 'root_ui_id': owner,
                            'symbol': names[owner],
                            'file': f'frontend/src/{folder}/{names[owner]}.tsx', 'ui_ids': []}
        membership[key] = owner
        units[owner]['ui_ids'].append(key)
        for child in row['children']:
            visit(child, owner, active | {key})

    visit(root, root, set())
    if set(ui) - membership.keys():
        raise ValueError('Unattached UI nodes: ' + ', '.join(sorted(set(ui) - membership.keys())))
    allocate(list(units), set())
    for owner, unit in units.items():
        folder = 'pages' if ui[owner]['kind'] == 'screen' else 'components'
        unit['symbol'] = names[owner]
        unit['file'] = f'frontend/src/{folder}/{names[owner]}.tsx'
        imports = {names[child] for key in unit['ui_ids'] for child in ui[key]['children']
                   if membership[child] != owner}
        allocate([key for key in unit['ui_ids'] if key != owner],
                 {names[owner], 'React', 'UIBoundary', 'UIProps', *imports})
    return {'units': list(units.values()),
            'ui_symbols': names,
            'ui_files': {key: units[owner]['file'] for key, owner in membership.items()},
            'ui_units': membership}


def plan_behavior(ir: dict[str, Any], ui_plan: dict[str, Any]) -> dict[str, Any]:
    functions = {row['id']: row for row in ir['functions']}
    users: dict[str, set[str]] = {key: set() for key in functions}
    event_files = {row['id']: ui_plan['ui_files'][row['ui_id']] for row in frontend_events(ir)}
    for event in frontend_events(ir):
        pending, seen = [call['target_id'] for call in event['handlers']], set()
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            row = functions[key]
            users[key].add(event_files[event['id']])
            pending.extend(call['target_id'] for call in row['calls'] if call['kind'] == 'FUNC')
    # Dependencies of a shared/unattached function must remain shared: no shared
    # module should import a page merely to get a nested helper registered.
    shared = {key for key, paths in users.items() if len(paths) != 1}
    pending = list(shared)
    while pending:
        key = pending.pop()
        for call in functions[key]['calls']:
            target = call['target_id']
            if call['kind'] == 'FUNC' and target not in shared:
                shared.add(target)
                pending.append(target)
    symbols: dict[str, str] = {}
    occupied = {name.casefold() for name in ui_plan['ui_symbols'].values()}
    for row in sorted([*ir['functions'], *frontend_events(ir)], key=lambda row: row['id']):
        value = row.get('name') if row['id'] in functions else None
        if not value:
            value = re.split(r'(?:^|[.:])(?:fn|function|event)[.:]', row['id'])[-1]
            value = re.sub(r'^(?:ROOT|REQ[-_][\d._-]+)[.:]*', '', value, flags=re.IGNORECASE)
        base = ('handle' if row['id'] not in functions else 'run') + _pascal(value)
        name = base
        if name.casefold() in occupied:
            name += hashlib.sha256(row['id'].encode()).hexdigest()[:6]
        symbols[row['id']] = name
        occupied.add(name.casefold())
    function_files = {key: f'frontend/src/functions/{symbols[key]}.ts' if key in shared
                      else next(iter(users[key])) for key in functions}
    for unit in ui_plan['units']:
        unit['event_ids'] = sorted(key for key, path in event_files.items() if path == unit['file'])
        unit['function_ids'] = sorted(key for key, path in function_files.items() if path == unit['file'])
    return {'event_files': event_files, 'function_files': function_files,
            'behavior_symbols': symbols, 'shared_function_ids': sorted(shared),
            'function_users': {key: sorted(paths) for key, paths in users.items()}}


def api_symbols(apis: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    occupied: set[str] = set()
    for key, api in sorted(apis.items()):
        local = str(api.get('name') or key.rsplit('.', 1)[-1])
        base = _pascal(local)
        name = base[:1].lower() + base[1:] + 'Api'
        if name.casefold() in occupied:
            name += hashlib.sha256(key.encode()).hexdigest()[:6]
        names[key] = name
        occupied.add(name.casefold())
    return names
