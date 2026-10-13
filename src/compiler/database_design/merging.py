"""Merge requirement-owned fields without rejecting semantic conflicts."""
from __future__ import annotations

import copy
from typing import Any

from .common import _compact_properties


def merge_field_contributions(entity: str, contributions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [contributions[rid] for rid in sorted(contributions)]
    props: dict[str, Any] = {}
    declarations = [_compact_properties(row['properties']) for row in rows]
    for key in ('min_length', 'minimum', 'max_length', 'maximum'):
        values = [row[key] for row in declarations if key in row]
        if values:
            props[key] = (max if key in {'min_length', 'minimum'} else min)(values)
    enums = [set(row['enum']) for row in declarations if 'enum' in row]
    if enums:
        allowed = set.intersection(*enums)
        props['enum'] = sorted(allowed)
    if any(row.get('unique') is True for row in declarations):
        props['unique'] = True
        comparisons = {row.get('comparison', 'exact') for row in declarations if row.get('unique') is True}
        comparison = sorted(comparisons)[0]
        if rows[0]['type'] == 'string':
            props['comparison'] = comparison
    defaults = [row['default'] for row in declarations if 'default' in row]
    if defaults:
        props['default'] = copy.deepcopy(defaults[0])
    result = {
        'name': rows[0]['name'], 'type': rows[0]['type'],
        'nullable': all(row['nullable'] for row in rows),
        # One stable domain description; per-requirement wording remains in the ledger.
        'description': next((row['description'].strip() for row in rows if row['description'].strip()), ''),
        'properties': props,
    }
    return result
