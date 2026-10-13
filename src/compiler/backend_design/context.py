"""Small non-embedding retrieval and requirement-specific database context."""
import json
import re


def terms(value):
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', json.dumps(value, ensure_ascii=False))
    return set(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', text.lower()))


def database_context(database, rid):
    access = database.get('access', {}).get(rid, {'read': [], 'write': []})
    entities = [row for row in database['entities'] if rid in row['requirement_ids']
                or any(target.split('.')[0] == row['key'] for operation in ('read', 'write') for target in access.get(operation, []))]
    return {'read': access.get('read', []), 'write': access.get('write', []), 'entities': entities}


def retrieve(ir, kind, requirement, data, accesses, k=6):
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
