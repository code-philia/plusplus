"""Output shape, typed interfaces and whole-symbol-table consistency checks."""
import json
import re


def shape_errors(value, schema, path='$'):
    if 'enum' in schema and value not in schema['enum']:
        return [f'{path}: invalid enum value {value!r}']
    kind = schema.get('type')
    if kind and not isinstance(value, {'object': dict, 'array': list, 'string': str, 'boolean': bool}[kind]):
        return [f'{path}: expected {kind}']
    errors = []
    if kind == 'object':
        errors += [f'{path}: missing {key}' for key in schema['required'] if key not in value]
        errors += [f'{path}: unexpected {key}' for key in value if key not in schema['properties']]
        for key in value.keys() & schema['properties'].keys():
            errors += shape_errors(value[key], schema['properties'][key], path + '.' + key)
    if kind == 'array':
        for index, item in enumerate(value):
            errors += shape_errors(item, schema['items'], f'{path}[{index}]')
    return errors


def normalize_contract(raw):
    """Fill omitted object defaults without overriding explicit model decisions."""
    value = json.loads(raw)
    def visit(schema):
        if not isinstance(schema, dict):
            return
        if schema.get('type') == 'object':
            schema.setdefault('properties', {})
            schema.setdefault('required', [])
            schema.setdefault('additionalProperties', False)
        properties = schema.get('properties')
        if isinstance(properties, dict):
            for child in properties.values():
                visit(child)
        if 'items' in schema:
            visit(schema['items'])
        if isinstance(schema.get('anyOf'), list):
            for child in schema['anyOf']:
                visit(child)
    visit(value)
    return json.dumps(value, ensure_ascii=False)


def parse_contract(raw, label, definitions, *, interface=True, payload=False):
    if not isinstance(raw, str):
        raise ValueError(f'{label} must be a JSON string')
    value = json.loads(normalize_contract(raw))
    if interface and (not isinstance(value, dict) or value.get('type') != 'object'):
        raise ValueError(f'{label} must contain an object JSON Schema')
    def visit(schema, active=frozenset(), *, parameter=False):
        if not isinstance(schema, dict):
            raise ValueError(f'{label}: schema node must be an object')
        allowed = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'anyOf', '$ref', 'format', 'description', 'source'}
        if schema.keys() - allowed:
            raise ValueError(f'{label}: unsupported schema keys {sorted(schema.keys() - allowed)}')
        if 'source' in schema and (not parameter or schema['source'] not in {'path', 'query', 'header', 'body'}):
            raise ValueError(f'{label}: source is allowed only on top-level API payload parameters')
        if 'description' in schema and not isinstance(schema['description'], str):
            raise ValueError(f'{label}: description must be a string')
        if 'format' in schema and (schema.get('type') != 'string' or schema['format'] not in {'date', 'date-time', 'uuid'}):
            raise ValueError(f'{label}: format must be date/date-time/uuid on a string')
        if 'enum' in schema:
            options = schema['enum']
            kind = schema.get('type')
            matches = {
                'string': lambda item: isinstance(item, str),
                'integer': lambda item: type(item) is int,
                'number': lambda item: type(item) in (int, float),
                'boolean': lambda item: type(item) is bool,
                'null': lambda item: item is None,
            }
            if (not isinstance(options, list) or not options or kind not in matches
                    or any(not matches[kind](item) for item in options)
                    or len({json.dumps(item) for item in options}) != len(options)):
                raise ValueError(f'{label}: enum must contain unique values matching its scalar type')
        if '$ref' in schema:
            if schema.keys() - {'$ref', 'description', 'source'}:
                raise ValueError(f'{label}: $ref cannot redefine the referenced type')
            key = schema['$ref']
            if not isinstance(key, str) or not key.startswith('#/types/') or key[8:] not in definitions:
                raise ValueError(f'{label}: unknown type reference {key}')
            name = key[8:]
            if name in active:
                raise ValueError(f'{label}: recursive type {name}')
            visit(json.loads(normalize_contract(definitions[name])), active | {name})
        elif 'anyOf' in schema:
            if schema.keys() - {'anyOf', 'description', 'source'}:
                raise ValueError(f'{label}: anyOf cannot redefine its alternatives')
            if not isinstance(schema['anyOf'], list) or not schema['anyOf']:
                raise ValueError(f'{label}: anyOf needs alternatives')
            for branch in schema['anyOf']:
                visit(branch, active)
        elif schema.get('type') == 'object':
            props, required = schema.get('properties'), schema.get('required')
            if not isinstance(props, dict) or not isinstance(required, list) or any(not isinstance(key, str) for key in required) or set(required) - props.keys() or len(set(required)) != len(required) or schema.get('additionalProperties') is not False:
                raise ValueError(f'{label}: properties must be an object, required must contain unique existing property names, and additionalProperties must be false')
            for key, child in props.items():
                if not re.fullmatch(r'[a-z_][a-z0-9_]*', key):
                    raise ValueError(f'{label}: invalid parameter name {key}')
                visit(child, active, parameter=payload and schema is value)
        elif schema.get('type') == 'array':
            visit(schema.get('items'), active)
        elif schema.get('type') not in {'string', 'integer', 'number', 'boolean', 'null'}:
            raise ValueError(f'{label}: unsupported type {schema.get("type")}')
    visit(value)
    return value


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
