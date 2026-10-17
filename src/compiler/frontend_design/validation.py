"""Tree, scope, reference and typed-state checks used inside every repair loop."""
import json
import re
from compiler.backend_design.validation import parse_contract
from .contracts import KINDS, TRIGGERS


def ui_index(root):
    nodes, parents = {}, {}
    def visit(row, parent=None):
        key = row['id']
        if key in nodes:
            raise ValueError(f'Duplicate or cyclic UI node: {key}')
        nodes[key], parents[key] = row, parent
        for child in row['children']:
            visit(child, key)
    visit(root)
    return nodes, parents


def within(key, owner, parents):
    while key is not None:
        if key == owner:
            return True
        key = parents.get(key)
    return False


def value_matches(value, schema, definitions):
    if '$ref' in schema:
        return value_matches(value, json.loads(definitions[schema['$ref'][8:]]), definitions)
    if 'anyOf' in schema:
        return any(value_matches(value, branch, definitions) for branch in schema['anyOf'])
    kind = schema.get('type')
    valid = {'string': isinstance(value, str), 'integer': type(value) is int,
             'number': type(value) in (int, float), 'boolean': type(value) is bool,
             'null': value is None, 'object': isinstance(value, dict), 'array': isinstance(value, list)}.get(kind, False)
    if not valid:
        return False
    if 'enum' in schema and value not in schema['enum']:
        return False
    if kind == 'object':
        return (not (set(value) - schema['properties'].keys()) and not (set(schema['required']) - value.keys())
                and all(value_matches(item, schema['properties'][key], definitions) for key, item in value.items()))
    if kind == 'array':
        return all(value_matches(item, schema['items'], definitions) for item in value)
    if kind == 'string' and 'format' in schema:
        try:
            if schema['format'] == 'uuid':
                from uuid import UUID
                UUID(value)
            elif schema['format'] == 'date':
                from datetime import date
                date.fromisoformat(value)
            else:
                from datetime import datetime
                datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return False
    return True


def validate_ir(ir, backend):
    errors = []
    try:
        nodes, parents = ui_index(ir['ui'])
    except (ValueError, KeyError, TypeError) as exc:
        return [str(exc)]
    if ir['ui']['kind'] != 'app':
        errors.append('UI root must be app')
    page_names = set()
    for page in ir['ui']['children']:
        if page['kind'] != 'page' or page['name'] in page_names:
            errors.append(f'App children must be uniquely named pages: {page["name"]}')
        page_names.add(page['name'])
        local_names = set()
        page_nodes, _ = ui_index(page)
        for row in page_nodes.values():
            if row is page:
                continue
            if row['kind'] not in KINDS or not re.fullmatch(r'[A-Z][A-Za-z0-9]*', row['name']):
                errors.append(f'Invalid semantic UI kind/name: {row["id"]}')
            if row['name'].casefold() in local_names:
                errors.append(f'Duplicate UI name in page {page["name"]}: {row["name"]}')
            local_names.add(row['name'].casefold())
    definitions = {row['name']: row['schema'] for row in ir['types']}
    if len(definitions) != len(ir['types']):
        errors.append('Duplicate frontend type name')
    for row in ir['types']:
        try:
            if not re.fullmatch(r'[A-Z][A-Za-z0-9]*', row['name']):
                raise ValueError(f'Invalid type name: {row["name"]}')
            parse_contract(row['schema'], row['name'], definitions, interface=False)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
    data = {row['id']: row for row in ir['data']}
    handlers = {row['id']: row for row in ir['handlers']}
    events = {row['id']: row for row in ir['events']}
    all_ids = list(nodes) + [row['id'] for group in ('data', 'handlers', 'events') for row in ir[group]]
    if len(set(all_ids)) != len(all_ids):
        errors.append('Duplicate frontend symbol ID')
    handler_names, event_keys = set(), set()
    for row in ir['data']:
        # Stable data IDs identify state; lifting two states to a common owner
        # may give them the same display name. Lowering disambiguates properties.
        if row['owner'] not in nodes or not re.fullmatch(r'[a-z_][a-z0-9_]*', row['name']) or not row['spec'].strip():
            errors.append(f'Invalid state owner, name or spec: {row["id"]}')
        try:
            schema = parse_contract(row['schema'], row['name'], definitions, interface=False)
            if not value_matches(json.loads(row['initial']), schema, definitions):
                errors.append(f'{row["id"]}: initial value does not match schema')
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
        if not any(row['id'] in ui['data'] for ui in nodes.values()):
            errors.append(f'Unbound data: {row["id"]}')
    apis = {row['id'] for row in backend.get('modules', []) if row['kind'] == 'API'}
    for row in ir['handlers']:
        if row['name'].casefold() in handler_names or not re.fullmatch(r'[A-Z][A-Za-z0-9]*', row['name']) or not row['spec'].strip():
            errors.append(f'Invalid/duplicate handler name or spec: {row["id"]}')
        handler_names.add(row['name'].casefold())
        for field, targets in (('read', data), ('write', data), ('apis', apis)):
            if len(set(row[field])) != len(row[field]) or set(row[field]) - set(targets):
                errors.append(f'{row["id"]}: unknown/duplicate {field} references')
        for direction in ('input', 'output'):
            try:
                parse_contract(row[direction], row['name'] + '.' + direction, definitions)
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(str(exc))
        if not any(event['handler'] == row['id'] for event in events.values()):
            errors.append(f'Unused handler: {row["id"]}')
    for row in ir['events']:
        key = row['ui'], row['trigger']
        if key in event_keys or row['trigger'] not in TRIGGERS or not row['name'].strip():
            errors.append(f'Invalid or duplicate UI trigger: {row["id"]}')
        event_keys.add(key)
        if row['ui'] not in nodes or row['handler'] not in handlers:
            errors.append(f'Dangling event: {row["id"]}')
            continue
        if row['id'] not in nodes[row['ui']]['events']:
            errors.append(f'Event missing from UI: {row["id"]}')
        handler = handlers[row['handler']]
        for target in handler['read'] + handler['write']:
            if target in data and not within(row['ui'], data[target]['owner'], parents):
                errors.append(f'{row["id"]}: state {target} is outside event UI lifetime scope')
    for row in nodes.values():
        if not row['description'].strip():
            errors.append(f'UI description required: {row["id"]}')
        for field in ('reqs', 'data', 'events'):
            if len(set(row[field])) != len(row[field]):
                errors.append(f'Duplicate UI {field}: {row["id"]}')
        for key in row['data']:
            if key not in data or not within(row['id'], data[key]['owner'], parents):
                errors.append(f'Unknown or out-of-scope UI data: {row["id"]} -> {key}')
        for key in row['events']:
            if key not in events or events[key]['ui'] != row['id']:
                errors.append(f'Nonreciprocal UI event: {row["id"]} -> {key}')
    return list(dict.fromkeys(errors))
