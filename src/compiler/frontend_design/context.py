"""Page-local contexts, compact summaries and small contract reuse catalogs."""
import copy
import json
from compiler.backend_design.context import database_context, retrieve, terms
from .validation import ui_index, within


def tree_summary(root):
    def size(row):
        return 1 + sum(size(child) for child in row['children'])
    def identity(row):
        return {key: row[key] for key in ('id', 'name', 'kind')}
    return {**identity(root), 'pages': [
        {**identity(page), 'major_subtrees': [identity(child) for child in
            sorted(page['children'], key=lambda child: (-size(child), child['id']))[:3]]}
        for page in root['children']]}


def region(ir, page_names):
    pages = [copy.deepcopy(page) for page in ir['ui']['children'] if page['name'] in page_names]
    ids = {ir['ui']['id']}
    for page in pages:
        ids.update(ui_index(page)[0])
    return pages, ids


def related_apis(backend, requirement, database):
    data = database_context(database, requirement['id'])
    direct = [row for row in backend['modules'] if row['kind'] == 'API' and requirement['id'] in row['reqs']]
    candidates = retrieve(backend, 'API', requirement, data, database.get('access', {}))
    return list({row['id']: copy.deepcopy(row) for row in direct + candidates}.values())


def select_types(ir, apis, contracts=()):
    definitions = {row['name']: row for row in ir['types']}
    used = set()
    def visit(value):
        if isinstance(value, dict):
            reference = value.get('$ref', '')
            if isinstance(reference, str) and reference.startswith('#/types/'):
                name = reference[8:]
                if name in definitions and name not in used:
                    used.add(name)
                    visit(json.loads(definitions[name]['schema']))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for api in apis:
        for field in ('payload', 'response'):
            visit(json.loads(api[field]))
    for raw in contracts:
        visit(json.loads(raw))
    return used


def state_context(ir, ids):
    return [copy.deepcopy(row) for row in ir['data'] if row['owner'] in ids]


def handler_context(ir, ids, data, apis, requirement, k=6):
    nodes, parents = ui_index(ir['ui'])
    bound = {key for key in ids if requirement['id'] in nodes[key]['reqs']}
    available_data, available_apis = {row['id'] for row in data}, {row['id'] for row in apis}
    events = [row for row in ir['events'] if row['ui'] in bound]
    direct = {row['handler'] for row in events}
    query = terms({key: requirement.get(key) for key in ('name', 'description', 'scenarios')})
    ranked = []
    for row in ir['handlers']:
        if set(row['read'] + row['write']) - available_data or set(row['apis']) - available_apis:
            continue
        if not any(all(within(ui, next(item['owner'] for item in data if item['id'] == target), parents)
                       for target in row['read'] + row['write']) for ui in bound):
            continue
        score = len(query & terms(row['name'] + ' ' + row['spec']))
        score += 5 * len(set(row['read'] + row['write']) & available_data)
        if row['id'] in direct or score:
            ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], item[1]['id']))
    selected = {row['id']: row for _, row in ranked[:k]}
    selected.update({row['id']: row for _, row in ranked if row['id'] in direct})
    events = [row for row in events if row['handler'] in selected]
    return copy.deepcopy(list(selected.values())), copy.deepcopy(events)
