"""Output shape, typed interfaces and whole-symbol-table consistency checks."""
import json
import re


# Preserve the historical imports while shared validation lives with the IR.
from compiler.ir.shape_validation import shape_errors
from compiler.ir.type_contracts import normalize_contract, parse_contract


def route_key(row):
    return row['method'], re.sub(r':[a-zA-Z_][a-zA-Z0-9_]*', ':param', row['path'].rstrip('/') or '/')


def graph_errors(ir):
    errors, modules = [], {row['id']: row for row in ir['modules']}
    visiting, done = set(), set()
    def visit(key):
        if key in visiting:
            errors.append(f'Call cycle at {key}')
            return
        if key in done:
            return
        visiting.add(key)
        for target in modules[key]['callees']:
            if target not in modules:
                errors.append(f'Unknown callee {key} -> {target}')
                continue
            allowed = {'API': {'FUNC'}, 'FUNC': {'FUNC', 'DB'}, 'DB': {'DB'}}[modules[key]['kind']]
            if modules[target]['kind'] not in allowed:
                errors.append(f'Invalid call layer {key} -> {target}')
            visit(target)
        visiting.remove(key)
        done.add(key)
    for key in modules:
        visit(key)
    for row in ir['modules']:
        for direction, opposite in (('callers', 'callees'), ('callees', 'callers')):
            if len(set(row[direction])) != len(row[direction]):
                errors.append(f'Duplicate {direction} for {row["id"]}')
            for target in row[direction]:
                if target not in modules or row['id'] not in modules[target][opposite]:
                    errors.append(f'Nonreciprocal {direction} edge {row["id"]} -> {target}')
    return errors


def validate_ir(ir, database):
    definitions = {row['name']: row['schema'] for row in ir['types']}
    modules = ir['modules']
    names, ids, routes, errors = set(), set(), set(), []
    if len(definitions) != len(ir['types']):
        errors.append('Duplicate type names')
    for definition in ir['types']:
        try:
            if not re.fullmatch(r'[A-Z][A-Za-z0-9]*', definition['name']):
                raise ValueError(f'Invalid PascalCase type name: {definition["name"]}')
            parse_contract(definition['schema'], definition['name'], definitions, interface=False)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))
    targets = {entity['key'] for entity in database['entities']}
    targets.update(entity['key'] + '.' + field['name'] for entity in database['entities'] for field in entity['fields'])
    for row in modules:
        if row['name'].casefold() in names:
            errors.append(f'Globally duplicate module name: {row["name"]}')
        names.add(row['name'].casefold())
        if row['id'] in ids:
            errors.append(f'Duplicate ID {row["id"]}')
        ids.add(row['id'])
        if not re.fullmatch(r'[A-Z][A-Za-z0-9]*', row['name']) or not row['spec'].strip():
            errors.append(f'{row["name"]}: PascalCase name and nonempty spec required')
        if row['shared'] != bool(row['shared_reason'].strip()):
            errors.append(f'{row["name"]}: shared_reason must justify shared:true, otherwise be empty')
        try:
            if row['kind'] == 'API':
                if row['method'] not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}:
                    errors.append(f'{row["name"]}: unsupported HTTP method')
                key = route_key(row)
                if key in routes:
                    errors.append(f'Duplicate method/route {key}')
                routes.add(key)
                if (not row['path'].startswith('/') or any(c.isspace() or c in '?#{}' for c in row['path'])
                        or any(':' in segment and not re.fullmatch(r':[a-z_][a-z0-9_]*', segment)
                               for segment in row['path'].split('/'))):
                    errors.append(f'{row["name"]}: invalid absolute route')
                request = parse_contract(row['payload'], row['name'] + '.payload', definitions, payload=True)
                parse_contract(row['response'], row['name'] + '.response', definitions)
                properties = request['properties']
                if any(field.get('source') not in {'path', 'query', 'header', 'body'} for field in properties.values()):
                    errors.append(f'{row["name"]}: payload fields require source:path/query/header/body')
                placeholders = set(re.findall(r':([A-Za-z_][A-Za-z0-9_]*)', row['path']))
                path_fields = {key for key, field in properties.items() if field.get('source') == 'path'}
                if placeholders != path_fields or path_fields - set(request['required']):
                    errors.append(f'{row["name"]}: required payload path fields must match route parameters')
                if row['method'] in {'GET', 'HEAD'} and any(field.get('source') == 'body' for field in properties.values()):
                    errors.append(f'{row["name"]}: GET/HEAD cannot have body')
            else:
                for direction in ('input', 'output'):
                    parse_contract(row[direction], row['name'] + '.' + direction, definitions)
            if row['kind'] == 'DB':
                for operation in ('read', 'write'):
                    references = json.loads(row[operation])
                    if not isinstance(references, list) or any(not isinstance(ref, str) for ref in references):
                        raise ValueError(f'{row["name"]}.{operation}: expected JSON string array')
                    if len(set(references)) != len(references) or set(references) - targets:
                        raise ValueError(f'{row["name"]}.{operation}: duplicate or unknown database target')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            errors.append(str(exc))
    errors += graph_errors(ir)
    return list(dict.fromkeys(errors))
