"""Explicit navigation, shell and page-state contracts from accepted bundle evidence."""
import copy
import hashlib
from .delta import make_ui
from .validation import ui_index
from compiler.preprocessing.evidence import captures_for

BUNDLE_UI_INSTRUCTIONS = '''
The compiler supplies navigation_contract, shell and explicit page_state_contract.
Keep the shell outside page content. Preserve page IDs, source routes, menu labels and state IDs.
For an explicit state, selected_ui_tree is that state's region, not the entire page: design only
its contract and changes. A list/base remains visible behind a child dialog; sibling dialogs
are alternatives, never permanently co-visible. Parent/control IDs in loaded_ui_ids may be used
for bindings, but do not duplicate base content inside overlays. Reference recipes are evidence
for opening/closing states, not code to replay against the original service. Use generated
usePageState().openState(id, parameters)/closeState() for state transitions and selected record
context; never encode these as independent pages or grant permissions from route parameters.
Uncollected states retain explicit coverage metadata; do not invent their UI from nonexistent images.
Do not invent authorization gates from the exploration account or menu visibility.
'''


def symbol(prefix, key):
    return 'UI_BUNDLE_' + prefix + '_' + hashlib.sha256(key.encode()).hexdigest()[:12]


def seed_bundle_ui(ir, requirements):
    ui = requirements.get('bundle', {}).get('ui', {})
    if not ui:
        return []
    ir['ui']['name'] = requirements['nodes'][requirements['root_id']].get('name', 'Application')
    links = ui.get('links', [])
    manifest = {'version': 1, 'menu': copy.deepcopy(ui.get('menu', [])), 'pages': [],
                'shell': {'requirement_id': requirements['root_id'], 'states': [],
                          'design_system': copy.deepcopy(ui.get('design_system', {}))}, 'diagnostics': []}
    for link in links:
        if link['link_type'] == 'shared':
            state = ui['states'][link['state_id']]
            if not any(row['id'] == link['state_id'] for row in manifest['shell']['states']):
                manifest['shell']['states'].append({'id': link['state_id'], **copy.deepcopy(state),
                                                   'captures': captures_for(ui, link['state_id'])})
    page_states, routes = [], set()
    for pid, source in ui.get('pages', {}).items():
        name = ' / '.join(source.get('menu_path') or [source['title']])
        node = make_ui(symbol('P', pid), name, 'page', 'Page: ' + source['title'])
        direct = [row for row in links if ui['states'][row['state_id']]['page'] == pid]
        node['reqs'] = sorted({row['node_id'] for row in direct if row['link_type'] != 'shared'})
        ir['ui']['children'].append(node)
        route = source.get('route')
        if route:
            if not isinstance(route, str) or not route.startswith('/') or route.startswith('//') or '#' in route:
                raise ValueError(f'Unsupported bundle route for {pid}: {route!r}; use a local pathname/query')
            if route in routes:
                raise ValueError(f'Duplicate bundle route: {route}')
            routes.add(route)
        else:
            manifest['diagnostics'].append({'code': 'UI_ROUTE_UNRESOLVED', 'page_id': pid})
        page = {'id': pid, 'name': name, 'title': source['title'], 'route': route, 'ui_id': node['id'],
                'menu_path': copy.deepcopy(source.get('menu_path', [])), 'states': [],
                'requirement_ids': node['reqs'], 'coverage': 'linked' if direct else 'unlinked'}
        declared = source.get('states', [])
        active_ids = {row['state_id'] for row in direct if row['link_type'] != 'shared'}
        # Ancestors are structural context, without widening the leaf requirement ownership.
        pending = list(active_ids)
        while pending:
            parent = ui.get('states', {}).get(pending.pop(), {}).get('parent')
            if parent and parent not in active_ids:
                active_ids.add(parent); pending.append(parent)
        state_nodes = {}
        for entry in declared:
            sid = entry['id']; collected = sid in ui.get('states', {})
            detail = copy.deepcopy(ui.get('states', {}).get(sid, {}))
            record = {**copy.deepcopy(entry), **detail, 'id': sid, 'collected': collected,
                      'ui_id': symbol('S', sid) if sid in active_ids and collected else None,
                      'captures': captures_for(ui, sid) if collected else [],
                      'requirement_ids': sorted({row['node_id'] for row in direct if row['state_id'] == sid and row['link_type'] != 'shared'})}
            page['states'].append(record)
            if record['ui_id']:
                state_node = make_ui(record['ui_id'], 'State' + hashlib.sha256(sid.encode()).hexdigest()[:12],
                    'dialog' if record.get('kind') in ('dialog', 'modal') else 'region',
                    f'Explicit {record.get("kind", "page")} state {sid}: {record.get("item", "")}')
                state_node['reqs'] = record['requirement_ids']
                state_nodes[sid] = state_node
        for record in page['states']:
            if record['id'] in state_nodes:
                parent = state_nodes.get(record.get('parent'), node)
                parent['children'].append(state_nodes[record['id']])
        roots = [row['id'] for row in page['states'] if row['ui_id'] and not row.get('parent')]
        page['initial_state'] = roots[0] if roots else None
        if len(roots) > 1:
            manifest['diagnostics'].append({'code': 'UI_MULTIPLE_INITIAL_STATES', 'page_id': pid, 'candidates': roots})
        manifest['pages'].append(page)
        page_states.append({'name': name, 'ui_id': node['id'], 'page_id': pid, 'references': [], 'states': copy.deepcopy(page['states'])})
    ir['navigation'] = manifest
    errors = validate_navigation(ir)
    if errors:
        raise ValueError('; '.join(errors))
    return page_states


def state_region(ir, page):
    """Resolve one explicit state to its stable region and metadata."""
    if not page.get('state_id'):
        return None, None
    navigation = ir.get('navigation', {})
    owner = next(row for row in navigation['pages'] if row['id'] == page['page_id'])
    state = next(row for row in owner['states'] if row['id'] == page['state_id'])
    if not state['ui_id']:
        raise ValueError('Page state has no accepted collected UI binding: ' + state['id'])
    nodes, _ = ui_index(ir['ui'])
    return nodes[state['ui_id']], state


def validate_navigation(ir):
    manifest = ir.get('navigation')
    if manifest is None:
        return []
    nodes, parents = ui_index(ir['ui'])
    from .validation import within
    errors, page_ids, state_ids, routes = [], set(), set(), set()
    for page in manifest['pages']:
        if page['id'] in page_ids or page['ui_id'] not in nodes or nodes.get(page['ui_id'], {}).get('kind') != 'page':
            errors.append('Invalid/duplicate navigation page: ' + page['id'])
        page_ids.add(page['id'])
        if page['route']:
            if page['route'] in routes:
                errors.append('Duplicate page route: ' + page['route'])
            routes.add(page['route'])
        states = {row['id']: row for row in page['states']}
        for state in page['states']:
            if state['id'] in state_ids:
                errors.append('Duplicate explicit state: ' + state['id'])
            state_ids.add(state['id'])
            if state.get('ui_id') and (state['ui_id'] not in nodes or not within(state['ui_id'], page['ui_id'], parents)):
                errors.append('State UI lies outside its page: ' + state['id'])
            seen, current = set(), state
            while current:
                if current['id'] in seen:
                    errors.append('Cyclic page state ancestry: ' + state['id']); break
                seen.add(current['id'])
                parent = current.get('parent')
                if parent and parent not in states:
                    errors.append('Unknown page state parent: ' + str(parent)); break
                current = states.get(parent)
        initial = page.get('initial_state')
        if initial and (initial not in states or not states[initial].get('ui_id')):
            errors.append('Initial page state is not rendered: ' + str(initial))
    def menu_rows(rows):
        for row in rows:
            if row.get('page') and row['page'] not in page_ids:
                errors.append('Menu references an unknown page: ' + row['page'])
            menu_rows(row.get('children', []))
    menu_rows(manifest['menu'])
    return errors


def navigation_context(ir, rid):
    manifest = ir.get('navigation')
    if not manifest:
        return {}
    pages = []
    for page in manifest['pages']:
        if rid in page['requirement_ids']:
            pages.append(copy.deepcopy(page))
        else:
            pages.append({key: copy.deepcopy(page[key]) for key in ('id', 'title', 'route', 'ui_id', 'coverage')})
    return {'version': manifest['version'], 'menu': copy.deepcopy(manifest['menu']),
            'pages': pages, 'shell': copy.deepcopy(manifest['shell']),
            'diagnostics': copy.deepcopy(manifest.get('diagnostics', []))}
