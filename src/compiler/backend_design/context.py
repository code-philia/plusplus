"""Small non-embedding retrieval and requirement-specific database context."""
import json
import re
import copy


def terms(value):
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', json.dumps(value, ensure_ascii=False))
    return set(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', text.lower()))


def database_context(database, rid):
    access = database.get('access', {}).get(rid, {'read': [], 'write': []})
    entities = [row for row in database['entities'] if rid in row['requirement_ids']
                or any(target.split('.')[0] == row['key'] for operation in ('read', 'write') for target in access.get(operation, []))]
    return {'read': access.get('read', []), 'write': access.get('write', []), 'entities': entities}


def retrieve(ir, kind, requirement, data, accesses, k=10):
    query = terms({key: requirement[key] for key in ('name', 'description', 'scenarios') if key in requirement})
    wanted = set(data['read']) | set(data['write'])
    scores = {}
    for rid, access in accesses.items():
        other = set(access.get('read', [])) | set(access.get('write', []))
        fields = wanted & other
        entities = {key.split('.')[0] for key in wanted} & {key.split('.')[0] for key in other}
        all_entities = {key.split('.')[0] for key in wanted | other}
        scores[rid] = (20 * len(fields) / max(1, len(wanted | other))
                       + 5 * len(entities) / max(1, len(all_entities)))
    candidates = []
    for row in ir['modules']:
        if row['kind'] != kind:
            continue
        score = max((scores.get(rid, 0) for rid in row['reqs']), default=0)
        score += len(query & terms(row['name'] + ' ' + row['spec']))
        if row['shared']:
            score += 2
        if score:
            candidates.append((score, row))
    candidates.sort(key=lambda pair: (-pair[0], pair[1]['id']))
    return [row for _, row in candidates[:k]]


def module_index(modules):
    """Compact discovery only; full contracts are loaded on exact matches."""
    return [{key: row[key] for key in ('id', 'kind', 'name', 'method', 'path') if key in row}
            for row in modules]


def expand_module_context(ir, kind, payload, output):
    """Load known references and name/route collisions without a new model protocol."""
    from .validation import route_key
    modules = {row['id']: row for row in ir['modules']}
    wanted = {row['id'] for row in output['reuse'] if row['id'] in modules}
    for row in output['new_modules']:
        wanted.update(key for field in ('callers', 'callees') for key in row.get(field, []) if key in modules)
    conflicts = []
    for proposed in output['new_modules']:
        for existing in modules.values():
            same_name = proposed['name'].casefold() == existing['name'].casefold()
            same_route = kind == 'API' and existing['kind'] == 'API' and route_key(proposed) == route_key(existing)
            if same_name or same_route:
                wanted.add(existing['id'])
                conflicts.append(f"{proposed['name']} matches existing {existing['id']} ({existing['name']}) "
                                 f"by {'name and route' if same_name and same_route else 'name' if same_name else 'route'}. "
                                 'Reuse unchanged when compatible; otherwise use a distinct responsibility/name/route.')
    loaded = {row['id'] for field in ('reuse_candidates', 'upstream_modules', 'reference_catalog', 'conflict_contracts')
              for row in payload.get(field, [])}
    added = set()

    def append(field, key):
        if key not in {row['id'] for row in payload.get(field, [])}:
            payload.setdefault(field, []).append(copy.deepcopy(modules[key]))
            added.add(key)

    downstream = {'API': set(), 'FUNC': {'FUNC', 'DB'}, 'DB': {'DB'}}[kind]
    for key in sorted(wanted):
        row = modules[key]
        if row['kind'] == kind:
            append('reuse_candidates', key)
        elif row['kind'] in downstream:
            append('reference_catalog', key)
        elif key not in loaded:
            append('conflict_contracts', key)
    # Keep the established collaboration visible when reviewing unchanged reuse.
    pending, seen = list(wanted), set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        for target in modules[key]['callees']:
            if target in modules:
                if modules[target]['kind'] in downstream:
                    append('reference_catalog', target)
                pending.append(target)
    return sorted(added), conflicts
