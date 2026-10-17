"""Apply a layer delta to a copy; no model reply can partially alter committed IR."""
import copy
import json
from .validation import ui_index


def associate(row, rid):
    row['reqs'] = sorted(set(row['reqs']) | {rid})


def unique(values, label):
    if len(set(values)) != len(values):
        raise ValueError(f'Duplicate {label}')


def append_types(ir, output, contracts, payload):
    definitions = {row['name']: row['schema'] for row in ir['types']}
    new_names = set()
    for row in output['new_types']:
        if row['name'] in definitions:
            raise ValueError(f'Cannot overwrite type: {row["name"]}')
        definitions[row['name']] = row['schema']
        new_names.add(row['name'])
        ir['types'].append(copy.deepcopy(row))
    visited = set()
    allowed = {row['name'] for row in payload['existing_types']} | new_names
    def visit(value):
        if isinstance(value, dict):
            reference = value.get('$ref', '')
            if isinstance(reference, str) and reference.startswith('#/types/'):
                name = reference[8:]
                if name not in allowed:
                    raise ValueError(f'Unknown or unloaded type reference: {name}')
                if name in definitions and name not in visited:
                    visited.add(name)
                    visit(json.loads(definitions[name]))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for raw in contracts:
        visit(json.loads(raw))
    for row in output['new_types']:
        visit(json.loads(row['schema']))
    if new_names - visited:
        raise ValueError(f'Unused new types: {sorted(new_names - visited)}')


def make_ui(key, name, kind, description):
    return {'id': key, 'name': name, 'kind': kind, 'description': description,
            'reqs': [], 'children': [], 'data': [], 'events': []}


def coordinate_state_owners(ir):
    """Lift state to the common scope of its consumers and event sites.

    Ownership describes lifetime, independently of requirement traceability.
    Include the old owner so incremental coordination never narrows its lifetime.
    """
    nodes, parents = ui_index(ir['ui'])
    uses = {row['id']: {row['owner']} for row in ir['data']}
    for node in nodes.values():
        for key in node['data']:
            if key in uses:
                uses[key].add(node['id'])
    handlers = {row['id']: row for row in ir['handlers']}
    for event in ir['events']:
        handler = handlers.get(event['handler'], {})
        for key in handler.get('read', []) + handler.get('write', []):
            if key in uses:
                uses[key].add(event['ui'])

    def ancestors(key):
        result = set()
        while key is not None:
            result.add(key)
            key = parents.get(key)
        return result

    for row in ir['data']:
        sites = uses[row['id']]
        if not sites <= nodes.keys():
            continue  # Existing reference validation reports invalid nodes.
        common = set.intersection(*(ancestors(key) for key in sites))
        owner = row['owner']
        while owner not in common:
            owner = parents[owner]
        row['owner'] = owner


def apply_ui(base, output, rid, payload):
    trial = copy.deepcopy(base)
    nodes, _ = ui_index(trial['ui'])
    loaded = set(payload['loaded_ui_ids'])
    if payload['page_mode'] == 'bind' and (output['add_nodes'] or output['updates']):
        raise ValueError('Already parsed page/reference permits requirement bindings only')
    aliases = {}
    for source in output['add_nodes']:
        if source['name'] in aliases:
            raise ValueError(f'Duplicate new UI alias: {source["name"]}')
        key = f'UI_{len(nodes):06d}'
        aliases[source['name']] = key
        nodes[key] = make_ui(key, source['name'], source['kind'], source['description'])
    def resolve(key):
        if key.startswith('@'):
            if key[1:] not in aliases:
                raise ValueError(f'Unknown UI alias: {key}')
            return aliases[key[1:]]
        if key not in loaded:
            raise ValueError(f'Unloaded UI reference: {key}')
        return key
    for source in output['add_nodes']:
        nodes[resolve(source['parent'])]['children'].append(nodes[aliases[source['name']]])
    actual, _ = ui_index(trial['ui'])
    if set(aliases.values()) - actual.keys():
        raise ValueError('New UI nodes must be reachable from the current page, without parent cycles')
    for operation in ('updates', 'bindings'):
        seen = set()
        for row in output[operation]:
            if operation == 'updates' and row['id'] not in loaded:
                raise ValueError('updates must target existing loaded UI IDs')
            key = resolve(row['id'])
            if key in seen or not row['description'].strip():
                raise ValueError(f'Duplicate {operation} target or empty description: {key}')
            seen.add(key)
            nodes[key]['description'] = row['description']
            if operation == 'bindings':
                associate(nodes[key], rid)
    if not output['bindings']:
        raise ValueError('Bind the current requirement to relevant existing/new UI nodes')
    return trial


def apply_data(base, output, rid, payload):
    trial = copy.deepcopy(base)
    nodes, _ = ui_index(trial['ui'])
    loaded = set(payload['loaded_ui_ids'])
    available = {row['id'] for row in payload['existing_data']}
    symbols = {row['id']: row for row in trial['data']}
    aliases, selected = {}, set()
    append_types(trial, output, [row['schema'] for row in output['new_data']], payload)
    for row in output['new_data']:
        if row['name'] in aliases or row['owner'] not in loaded:
            raise ValueError(f'Duplicate data alias or unloaded owner: {row["name"]}')
        key = f'DATA_{len(symbols) + 1:06d}'
        symbol = {**copy.deepcopy(row), 'id': key, 'reqs': [rid]}
        symbols[key], aliases[row['name']] = symbol, key
        trial['data'].append(symbol)
        selected.add(key)
    unique(output['reuse_data'], 'reuse_data')
    for key in output['reuse_data']:
        if key not in available:
            raise ValueError(f'Unknown reuse data: {key}')
        associate(symbols[key], rid)
        selected.add(key)
    bound, seen = set(), set()
    for binding in output['bindings']:
        key = binding['data']
        key = aliases.get(key[1:]) if key.startswith('@') else key
        ui = binding['ui']
        if key not in selected or ui not in loaded or (ui, key) in seen:
            raise ValueError('Data bindings must uniquely connect loaded UI to selected data')
        seen.add((ui, key))
        if key not in nodes[ui]['data']:
            nodes[ui]['data'].append(key)
        associate(nodes[ui], rid)
        bound.add(key)
    new_ids = set(aliases.values())
    if new_ids - bound:
        raise ValueError(f'New data has no consumer: {sorted(new_ids - bound)}')
    for key in selected - bound:
        if not any(key in nodes[ui]['data'] for ui in loaded):
            raise ValueError(f'Reused data has no consumer for this requirement: {key}')
    coordinate_state_owners(trial)
    return trial


def apply_interaction(base, output, rid, payload):
    if output['blocked_reason'].strip():
        raise ValueError('Unresolved frontend capability: ' + output['blocked_reason'])
    trial = copy.deepcopy(base)
    nodes, _ = ui_index(trial['ui'])
    loaded = set(payload['loaded_ui_ids'])
    allowed_data = {row['id'] for row in payload['existing_data']}
    allowed_apis = {row['id'] for row in payload['api_contracts']}
    allowed_handlers = {row['id'] for row in payload['reuse_handlers']}
    allowed_events = {row['id'] for row in payload['existing_events']}
    handlers = {row['id']: row for row in trial['handlers']}
    events = {row['id']: row for row in trial['events']}
    data = {row['id']: row for row in trial['data']}
    selected, aliases, used = set(), {}, set()
    append_types(trial, output, [row[field] for row in output['new_handlers'] for field in ('input', 'output')], payload)
    for row in output['new_handlers']:
        if row['name'] in aliases:
            raise ValueError(f'Duplicate handler alias: {row["name"]}')
        if (set(row['read'] + row['write']) - allowed_data) or (set(row['apis']) - allowed_apis):
            raise ValueError(f'{row["name"]}: references unloaded data/API')
        key = f'HANDLER_{len(handlers) + 1:06d}'
        symbol = {**copy.deepcopy(row), 'id': key, 'reqs': [rid]}
        handlers[key], aliases[row['name']] = symbol, key
        trial['handlers'].append(symbol)
        selected.add(key)
    unique(output['reuse_handlers'], 'reuse_handlers')
    unique(output['reuse_events'], 'reuse_events')
    for key in output['reuse_handlers']:
        if key not in allowed_handlers:
            raise ValueError(f'Unknown reuse handler: {key}')
        associate(handlers[key], rid)
        selected.add(key)
    unique([row['id'] for row in output['handler_updates']], 'handler_updates')
    for row in output['handler_updates']:
        key = row['id']
        if key not in allowed_handlers or key in selected or not row['spec'].strip():
            raise ValueError(f'Invalid handler update; loaded, distinct ID and additional spec required: {key}')
        if any(event['ui'] not in loaded for event in events.values() if event['handler'] == key):
            raise ValueError(f'Cannot update handler with unloaded event sites: {key}')
        if set(row['read'] + row['write']) - allowed_data or set(row['apis']) - allowed_apis:
            raise ValueError(f'Handler update requires unloaded state/API: {key}')
        for field in ('read', 'write', 'apis'):
            unique(row[field], 'handler update ' + field)
            handlers[key][field] = list(dict.fromkeys(handlers[key][field] + row[field]))
        handlers[key]['spec'] += '\nAdditional behavior for ' + rid + ': ' + row['spec']
        associate(handlers[key], rid)
        selected.add(key)
    for row in output['new_events']:
        reference = row['handler']
        handler = aliases.get(reference[1:]) if reference.startswith('@') else reference
        if row['ui'] not in loaded:
            raise ValueError(f'Event {row["name"]}: UI {row["ui"]} is not loaded; choose an exact event_ui_candidates ID')
        if handler not in selected:
            raise ValueError(f'Event {row["name"]}: handler {reference} is not selected; '
                             'use @Name matching new_handlers.name, or select an existing exact ID '
                             'in output reuse_handlers/handler_updates before attaching a new event')
        key = f'EVENT_{len(events) + 1:06d}'
        symbol = {**copy.deepcopy(row), 'handler': handler, 'id': key, 'reqs': [rid]}
        events[key] = symbol
        trial['events'].append(symbol)
        nodes[row['ui']]['events'].append(key)
        associate(nodes[row['ui']], rid)
        used.add(handler)
    for key in output['reuse_events']:
        if key not in allowed_events or events[key]['ui'] not in loaded:
            raise ValueError(f'Unknown or unrelated reuse event: {key}')
        handler = events[key]['handler']
        if handler not in allowed_handlers:
            raise ValueError(f'Reused event needs a loaded handler contract: {key}')
        associate(events[key], rid)
        associate(handlers[handler], rid)
        associate(nodes[events[key]['ui']], rid)
        used.add(handler)
    if selected - used:
        raise ValueError(f'Unattached selected handlers: {sorted(selected - used)}')
    for key in used:
        for target in handlers[key]['read'] + handlers[key]['write']:
            if target not in allowed_data:
                raise ValueError(f'Reused handler requires unloaded state: {target}')
            associate(data[target], rid)
        if set(handlers[key]['apis']) - allowed_apis:
            raise ValueError(f'Reused handler requires unloaded API: {key}')
    coordinate_state_owners(trial)
    return trial
