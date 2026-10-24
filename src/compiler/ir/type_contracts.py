"""JSON Schema contracts shared by design passes and lowering."""
import json
import re
from typing import TypedDict


class TypeDefinition(TypedDict):
    """Named JSON Schema stored as a JSON string, with #/types/Name references."""
    name: str
    schema: str


def iter_refs(raw, *, named_only=False):
    """Yield references in document order; named_only ignores non-type references."""
    def visit(value):
        if isinstance(value, dict):
            if '$ref' in value:
                reference = value['$ref']
                if not named_only or isinstance(reference, str) and reference.startswith('#/types/'):
                    yield reference[8:]
            for item in value.values():
                yield from visit(item)
        elif isinstance(value, list):
            for item in value:
                yield from visit(item)
    return visit(json.loads(raw))


def refs(raw, *, named_only=False):
    """Collect references, preserving legacy slicing/errors unless named_only is set."""
    return set(iter_refs(raw, named_only=named_only))


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


# Preserve the legacy identities for existing callers and serialized references.
normalize_contract.__module__ = 'compiler.backend_design.validation'
parse_contract.__module__ = 'compiler.backend_design.validation'
