"""JSON structure checks and call-graph traversal helpers."""
from __future__ import annotations

from typing import Any

from .contracts import IR_SCHEMA


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
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            errors.append(f'{path}: expected at most {schema["maxItems"]} items')
        if 'minItems' in schema and len(value) < schema['minItems']:
            errors.append(f'{path}: expected at least {schema["minItems"]} items')
        for index, item in enumerate(value):
            errors += shape_errors(item, schema['items'], f'{path}[{index}]', definitions)
    return errors


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
    """Check only JSON fields and value shapes; design rules stay in prompts."""
    return shape_errors(ir, IR_SCHEMA)
