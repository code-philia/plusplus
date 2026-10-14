"""Render validated JSON Schema to TypeScript; all named definitions are exported."""
import json
import re
from compiler.backend_design.validation import parse_contract


def ts_type(schema, namespace=""):
    if '$ref' in schema:
        return namespace + schema['$ref'][8:]
    if 'anyOf' in schema:
        return '(' + ' | '.join(ts_type(item, namespace) for item in schema['anyOf']) + ')'
    if 'enum' in schema:
        return '(' + ' | '.join(json.dumps(item, ensure_ascii=False) for item in schema['enum']) + ')'
    kind = schema['type']
    if kind == 'object':
        required = set(schema['required'])
        return '{ ' + ' '.join(json.dumps(name) + ('' if name in required else '?') + ': ' + ts_type(value, namespace) + ';'
                               for name, value in schema['properties'].items()) + ' }'
    if kind == 'array':
        return '(' + ts_type(schema['items'], namespace) + ')[]'
    return {'string': 'string', 'integer': 'number', 'number': 'number', 'boolean': 'boolean', 'null': 'null'}[kind]


def contract(raw, definitions, interface=True, namespace="", *, payload=False):
    return ts_type(parse_contract(raw, 'lowering contract', definitions, interface=interface, payload=payload), namespace)


def refs(raw):
    found = set()
    def visit(value):
        if isinstance(value, dict):
            if '$ref' in value:
                found.add(value['$ref'][8:])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(json.loads(raw))
    return found


def type_file(definitions):
    mapping = {row['name']: row['schema'] for row in definitions}
    if len(mapping) != len(definitions):
        raise ValueError('Duplicate type definitions')
    if any(not re.fullmatch(r'[A-Z][A-Za-z0-9]*', row['name']) for row in definitions):
        raise ValueError('Named types must use PascalCase identifiers')
    return '// Generated type contracts.\n' + '\n'.join(
        'export type ' + row['name'] + ' = ' + contract(row['schema'], mapping, False) + ';'
        for row in definitions) + '\n'
