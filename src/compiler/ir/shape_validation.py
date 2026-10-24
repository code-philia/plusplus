"""Shared structural checks for model decisions."""


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


# Preserve the legacy identity for introspection and serialized function references.
shape_errors.__module__ = 'compiler.backend_design.validation'
