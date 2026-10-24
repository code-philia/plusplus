"""Shared structural checks for model decisions."""


def shape_errors(value, schema, path='$'):
    if 'enum' in schema and value not in schema['enum']:
        return [f'{path}: invalid enum value {value!r}']
    if 'anyOf' in schema:
        if any(not shape_errors(value, option, path) for option in schema['anyOf']):
            return []
        return [f'{path}: does not match any allowed shape']
    kind = schema.get('type')
    kinds = kind if isinstance(kind, list) else [kind] if kind else []
    python_types = {'object': dict, 'array': list, 'string': str, 'boolean': bool,
                    'integer': int, 'number': (int, float), 'null': type(None)}
    matches = [name for name in kinds if isinstance(value, python_types[name])
               and not (name in {'integer', 'number'} and isinstance(value, bool))]
    if kinds and not matches:
        return [f'{path}: expected {kind}']
    kind = matches[0] if matches else None
    errors = []
    if kind == 'object':
        errors += [f'{path}: missing {key}' for key in schema.get('required', []) if key not in value]
        if schema.get('additionalProperties') is False:
            errors += [f'{path}: unexpected {key}' for key in value if key not in schema.get('properties', {})]
        for key in value.keys() & schema.get('properties', {}).keys():
            errors += shape_errors(value[key], schema.get('properties', {})[key], path + '.' + key)
    if kind == 'array':
        for index, item in enumerate(value):
            errors += shape_errors(item, schema['items'], f'{path}[{index}]')
    return errors
