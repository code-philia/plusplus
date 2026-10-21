"""Read the published logical ER contract without inventing missing relationship IDs."""
from __future__ import annotations

import hashlib
import re
from typing import Any


def symbol(value: str) -> str:
    value = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', value)
    value = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', value)
    value = re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')
    if not re.fullmatch(r'[a-z][a-z0-9_]*', value):
        raise ValueError(f'Unsupported ER identifier: {value!r}')
    return value


def parse_mermaid(text: str, path: str) -> dict[str, Any]:
    """Parse the serializer's Mermaid ER subset; reject unrecognized semantics."""
    entities, relationships = [], []
    current = None
    relationship_id = None
    header = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith('%% relationship_id:'):
            if current is not None or relationship_id is not None:
                raise ValueError(f'{path}:{number}: misplaced relationship ID')
            relationship_id = line.partition(':')[2].strip()
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', relationship_id):
                raise ValueError(f'{path}:{number}: invalid relationship ID')
            continue
        if not line or line.startswith('%%'):
            continue
        source = {'path': path, 'line': number}
        if line == 'erDiagram' and not header:
            header = True
            continue
        if not header:
            raise ValueError(f'{path}:{number}: expected erDiagram')
        if current is not None:
            if line == '}':
                current = None
                continue
            match = re.fullmatch(r'(\w+)\s+(\w+)(?:\s+(PK|FK|UK)(?:\s*,\s*(PK|FK|UK))*)?(?:\s+"([^"]*)")?', line)
            if not match:
                raise ValueError(f'{path}:{number}: unsupported ER attribute: {line}')
            kind, name = match.group(1, 2)
            qualifiers = line.split('"', 1)[0].split()[2:]
            if 'FK' in ','.join(qualifiers):
                raise ValueError(f'{path}:{number}: attribute FK has no explicit target; publish a relationship instead')
            description = match.group(5) or name
            # This bundle's serializer encodes nullability in the label.
            current['attributes'].append({
                'id': name, 'name': description.removesuffix(' (required)'),
                'logical_type': kind, 'nullable': not description.endswith(' (required)'),
                'primary_key': 'PK' in ','.join(qualifiers),
                'unique': 'UK' in ','.join(qualifiers), 'source': source,
            })
            continue
        match = re.fullmatch(r'(\w+)\s*\{', line)
        if match:
            if relationship_id is not None:
                raise ValueError(f'{path}:{number}: relationship ID must precede a relationship')
            current = {'id': match[1], 'name': match[1], 'attributes': [], 'source': source}
            entities.append(current)
            continue
        match = re.fullmatch(r'(\w+)\s+([|o}{]{2})(--|\.\.)([|o}{]{2})\s+(\w+)\s*:\s*"([^"]*)"', line)
        if not match:
            raise ValueError(f'{path}:{number}: unsupported ER syntax: {line}')
        left = {'||': '1', 'o|': '0..1', '|o': '0..1', '}o': '0..*', '}|': '1..*'}
        right = {'||': '1', 'o|': '0..1', '|o': '0..1', 'o{': '0..*', '|{': '1..*'}
        if match[2] not in left or match[4] not in right:
            raise ValueError(f'{path}:{number}: invalid ER cardinality')
        relationships.append({'id': relationship_id, 'name': match[6], 'source_ref': match[1],
                              'target_ref': match[5], 'source_cardinality': left[match[2]],
                              'target_cardinality': right[match[4]], 'source': source})
        relationship_id = None
    if current is not None or relationship_id is not None or not header or not entities:
        raise ValueError(f'{path}: incomplete ER diagram')
    return normalize_er({'entities': entities, 'relationships': relationships}, path)


def normalize_er(raw: dict[str, Any], path: str) -> dict[str, Any]:
    """Accept domain.er's JSON shape as the lossless companion to model.mmd."""
    entities, relationships, aliases, diagnostics = [], [], {}, []
    types = {'string': 'string', 'text': 'string', 'integer': 'integer', 'int': 'integer',
             'decimal': 'number', 'float': 'number', 'double': 'number', 'number': 'number',
             'boolean': 'boolean', 'bool': 'boolean', 'date': 'date', 'datetime': 'datetime',
             'json': 'json', 'uuid': 'uuid', 'file': 'string'}
    keys = set()
    for item in raw['entities']:
        key = symbol(item['id'])
        if key in keys:
            raise ValueError(f'{path}: colliding entity identifier {item["id"]}')
        keys.add(key)
        aliases[item['id']] = key
        fields, names = [], set()
        for field in item['attributes']:
            name = symbol(field['id'])
            if name in names:
                raise ValueError(f'{path}: colliding field {item["id"]}.{field["id"]}')
            names.add(name)
            kind = field['logical_type'].lower()
            if kind not in types:
                raise ValueError(f'{path}: unsupported logical type {kind}')
            if type(field.get('nullable', True)) is not bool or type(field.get('primary_key', False)) is not bool:
                raise ValueError(f'{path}: invalid field flags on {item["id"]}.{field["id"]}')
            if name == 'id' and kind != 'uuid':
                raise ValueError(f'{path}: id is reserved for the compiler UUID; use an explicit business-key name')
            fields.append({'name': name, 'source_id': field['id'], 'type': types[kind],
                           'logical_type': kind, 'nullable': False if field.get('primary_key') else field.get('nullable', True),
                           'primary_key': field.get('primary_key', False), 'unique': field.get('unique', False),
                           'description': field.get('name') or field['id'],
                           'source': field.get('source', {'path': path})})
            if kind in {'decimal', 'file'}:
                diagnostics.append({'severity': 'warning', 'code': 'ER_TYPE_MAPPING', 'path': path,
                    'entity': key, 'field': name,
                    'message': f'{item["id"]}.{field["id"]}: {kind} maps to {types[kind]}; '
                               + ('decimal precision and rounding remain a backend obligation.' if kind == 'decimal'
                                  else 'store an attachment reference; upload/storage behavior belongs to the backend.')})
        entities.append({'key': key, 'source_id': item['id'], 'description': item.get('name') or item['id'],
                         'fields': fields, 'source': item.get('source', {'path': path})})
    relation_keys = set()
    cardinalities = {'1': (1, 'one'), '0..1': (0, 'one'), '0..*': (0, 'many'), '1..*': (1, 'many')}
    for item in raw.get('relationships', []):
        if item['source_ref'] not in aliases or item['target_ref'] not in aliases:
            raise ValueError(f'{path}: relationship refers to an unknown entity: {item}')
        left, right = aliases[item['source_ref']], aliases[item['target_ref']]
        a, b = item['source_cardinality'], item['target_cardinality']
        if a not in cardinalities or b not in cardinalities:
            raise ValueError(f'{path}: unspecified/unsupported cardinality: {a}, {b}')
        # Mermaid endpoint counts describe instances at that endpoint. ARC's
        # parent_max instead describes children per parent, so swap counts.
        parent, child, parent_card, child_card = left, right, cardinalities[b], cardinalities[a]
        if parent_card[1] == 'one' and child_card[1] == 'many':
            parent, child, parent_card, child_card = right, left, cardinalities[a], cardinalities[b]
        digest = hashlib.sha256(f'{left}|{right}|{item["name"]}'.encode()).hexdigest()[:10]
        key = symbol(item['id']) if item.get('id') else f'er_{parent}_{child}_{digest}'
        if key in relation_keys:
            raise ValueError(f'{path}: duplicate relationship {key}')
        relation_keys.add(key)
        relationships.append({'key': key, 'source_id': item.get('id'), 'parent': parent, 'child': child,
            'parent_min': parent_card[0], 'parent_max': parent_card[1],
            'child_min': child_card[0], 'child_max': child_card[1],
            'type': {('one', 'one'): 'ONE_TO_ONE', ('many', 'one'): 'ONE_TO_MANY',
                     ('many', 'many'): 'MANY_TO_MANY'}[(parent_card[1], child_card[1])],
            'description': item['name'], 'source': item.get('source', {'path': path})})
    return {'entities': entities, 'relationships': relationships, 'entity_aliases': aliases,
            'diagnostics': diagnostics}
