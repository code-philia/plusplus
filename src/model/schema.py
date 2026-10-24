"""Stage-independent primitives for strict JSON Schema model responses."""


def obj(properties, required=None):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties,
            'required': list(properties) if required is None else required}


STRING = {'type': 'string'}
STRINGS = {'type': 'array', 'items': STRING}
