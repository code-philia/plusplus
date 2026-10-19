"""Seed design state from the bundle's published ER before model enrichment."""
from __future__ import annotations

import copy


def import_reference_er(state, requirements):
    bundle = requirements.get('bundle', {})
    er = bundle.get('er', {})
    nodes = requirements['nodes']

    def owners(ids):
        result = set()
        def visit(rid):
            children = nodes[rid].get('children_ids', [])
            if children:
                for child in children:
                    visit(child)
            else:
                result.add(rid)
        for rid in ids:
            visit(rid)
        # A published global fact with no leaf citation belongs to the real root,
        # rather than a fabricated requirement or every unrelated leaf.
        return sorted(result) or [requirements['root_id']]

    report = {'entities': [], 'relationships': [], 'source': requirements.get('source', {}),
              'diagnostics': [row for row in bundle.get('diagnostics', []) if row['code'].startswith(('ER_', 'BUNDLE_ER_'))]}
    for entity in er.get('entities', []):
        key = entity['key']
        rids = owners(entity.get('requirement_ids', []))
        state.entities[key] = {'key': key, 'description': entity['description'], 'fields': {},
                               'requirement_ids': [], 'read': [], 'write': []}
        for rid in rids:
            state._link_entity(rid, key)
            errors = []
            state._add_field(key, {'name': 'id', 'type': 'uuid', 'nullable': False,
                'description': 'Compiler-provided stable primary key.', 'properties': {},
                'origin': 'SYSTEM', 'references': None}, rid, errors, trace=False)
            if errors:
                raise ValueError('; '.join(errors))
        primary = [field['name'] for field in entity['fields'] if field['primary_key'] and field['name'] != 'id']
        for field in entity['fields']:
            if field['name'] == 'id':
                continue
            properties = {'unique': True} if field.get('unique') or (field['primary_key'] and len(primary) == 1) else {}
            declaration = {name: field[name] for name in ('name', 'type', 'nullable', 'description')}
            declaration['properties'] = properties
            state.reference_fields[f'{key}.{field["name"]}'] = copy.deepcopy(declaration)
            for rid in rids:
                errors = []
                state._add_field(key, {**declaration, 'origin': 'REQUIREMENT', 'references': None}, rid, errors)
                if errors:
                    raise ValueError('; '.join(errors))
        if len(primary) > 1:
            state.add_unique(key, primary, rids, 'Published composite business key.')
        report['entities'].append({'source_id': entity['source_id'], 'key': key,
            'source': entity['source'], 'requirement_ids': rids,
            'business_key': primary, 'identity': 'id', 'fields': copy.deepcopy(entity['fields'])})
    for relation in er.get('relationships', []):
        rids = owners(relation.get('requirement_ids', []))
        declaration = {name: relation[name] for name in ('key', 'parent', 'child', 'type',
            'parent_min', 'parent_max', 'child_min', 'child_max', 'description')}
        declaration['evidence_requirement_ids'] = rids
        for rid in rids:
            errors = []
            state.apply_relationships(rid, {'relationships': [declaration]}, errors)
            if errors:
                raise ValueError('; '.join(errors))
        state.reference_relationships[relation['key']] = copy.deepcopy(declaration)
        report['relationships'].append({**copy.deepcopy(relation), 'requirement_ids': rids})
    for rid, binding in bundle.get('bindings', {}).items():
        for crud in binding['crud']:
            for owner in owners([rid]):
                access = {'read': [crud['entity']] if 'read' in crud['operations'] else [],
                          'write': [crud['entity']] if set(crud['operations']) & {'create', 'update', 'delete'} else []}
                errors = []
                state._apply_access(owner, access, errors)
                if errors:
                    raise ValueError('; '.join(errors))
    return report


REFERENCE_INSTRUCTIONS = """
This run imports an existing logical ER model from the requirements bundle. The
reference_contract is source evidence, not another design proposal. Reuse its
normalized entity/field/relationship symbols; do not introduce synonymous copies.
Published field types, required flags and business keys remain in force. Add only
missing requirement-backed persistent facts, and strengthen compatible constraints.
An existing relationship must retain its key, endpoints and all four cardinalities;
do not bypass a conflicting source relationship by inventing a second key. Different
explicit semantic roles are allowed. Source IDs and unresolved reference diagnostics
are recorded separately; never claim an unresolved citation has been resolved.
decimal fields are currently represented as number: preserve their source logical
type as backend context for precision/rounding, not an invented database invariant.
"""
