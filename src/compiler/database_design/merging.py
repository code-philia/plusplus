"""Order-independent conjunction of requirement-owned field contributions."""
from __future__ import annotations

import copy
from typing import Any

from .common import _compact_properties
from .validation import _field_property_errors


def merge_field_contributions(entity: str, contributions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [contributions[rid] for rid in sorted(contributions)]
    for row in rows:
        problems = _field_property_errors(entity, row)
        if problems:
            raise ValueError('; '.join(problems))
    types = {row['type'] for row in rows}
    if len(types) != 1:
        raise ValueError('incompatible logical types: ' + ', '.join(sorted(types)))
    props: dict[str, Any] = {}
    declarations = [_compact_properties(row['properties']) for row in rows]
    for key in ('min_length', 'minimum', 'max_length', 'maximum'):
        values = [row[key] for row in declarations if key in row]
        if values:
            props[key] = (max if key in {'min_length', 'minimum'} else min)(values)
    enums = [set(row['enum']) for row in declarations if 'enum' in row]
    if enums:
        allowed = set.intersection(*enums)
        if not allowed:
            raise ValueError('empty allowed-value intersection')
        props['enum'] = sorted(allowed)
    if any(row.get('unique') is True for row in declarations):
        props['unique'] = True
        comparisons = {row.get('comparison', 'exact') for row in declarations if row.get('unique') is True}
        if len(comparisons) != 1:
            raise ValueError('incompatible business-key comparison semantics')
        comparison = comparisons.pop()
        if rows[0]['type'] == 'string':
            props['comparison'] = comparison
    defaults = [row['default'] for row in declarations if 'default' in row]
    if defaults:
        if any(type(value) is not type(defaults[0]) or value != defaults[0] for value in defaults):
            raise ValueError('incompatible storage defaults')
        props['default'] = copy.deepcopy(defaults[0])
    result = {
        'name': rows[0]['name'], 'type': rows[0]['type'],
        'nullable': all(row['nullable'] for row in rows),
        'description': '\n'.join(sorted({row['description'].strip() for row in rows if row['description'].strip()})),
        'properties': props,
    }
    problems = _field_property_errors(entity, result)
    if problems:
        raise ValueError('; '.join(problems))
    return result
