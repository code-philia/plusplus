"""Lower explicit bundle navigation without adding a router dependency."""
import copy
import json
from pathlib import Path
from compiler.lowering.planning import relative_import

RUNTIME_PATH = 'frontend/src/navigation/runtime.tsx'
CSS_PATH = 'frontend/src/navigation/shell.css'


def lower_navigation(frontend, component_by_ui, files, app_path):
    manifest = frontend.get('navigation')
    if not manifest:
        return None
    runtime_import = json.dumps(relative_import(app_path, RUNTIME_PATH, ''))
    imports = f'import {{ ApplicationShell }} from {runtime_import};\nimport "./navigation/shell.css";\n'
    pages, records = [], []
    source_pages = copy.deepcopy(manifest['pages'])
    menu = copy.deepcopy(manifest['menu'])
    owned = {page['ui_id'] for page in source_pages}
    for node in frontend['ui']['children']:
        if node['id'] not in owned:
            pid = 'legacy:' + node['id']
            source_pages.append({'id': pid, 'title': node['name'], 'route': '/__arc/page/' + node['id'],
                'ui_id': node['id'], 'initial_state': None, 'states': [], 'route_origin': 'compiler',
                'requirement_ids': node['reqs'], 'coverage': 'yaml'})
            menu.append({'label': node['name'], 'kind': 'page', 'page': pid})
    for page in source_pages:
        component = component_by_ui[page['ui_id']]
        imports += 'import { ' + component['export'] + ' } from ' + json.dumps(relative_import(app_path, component['file_path'], '')) + ';\n'
        config = {key: page[key] for key in ('id', 'title', 'route', 'initial_state')}
        config['states'] = [{key: state.get(key) for key in ('id', 'parent')} for state in page['states']]
        pages.append(json.dumps(config, ensure_ascii=False)[:-1] + ', component: ' + component['export'] + '}')
        records.append({**copy.deepcopy(page), 'file_path': component['file_path'], 'export': component['export']})
    title = frontend['ui']['name']
    files[app_path] = (imports + 'const pages = [' + ',\n'.join(pages) + '];\n'
        + 'const menu = ' + json.dumps(menu, ensure_ascii=False) + ';\n'
        + 'export function App() { return <ApplicationShell pages={pages} menu={menu} title={' + json.dumps(title, ensure_ascii=False)
        + '} />; }\nexport default App;\n')
    files[RUNTIME_PATH] = (Path(__file__).parent / 'assets/navigation.tsx').read_text()
    files[CSS_PATH] = ('.arc-application-shell{display:grid;grid-template-columns:240px minmax(0,1fr);grid-template-rows:56px minmax(0,1fr);min-height:100vh}'
        '.arc-application-header{grid-column:1/-1;display:flex;align-items:center;padding:0 24px;border-bottom:1px solid #e5e7eb}'
        '.arc-application-sidebar{padding:16px;border-right:1px solid #e5e7eb;overflow:auto}'
        '.arc-application-sidebar ul{list-style:none;padding-left:12px}.arc-application-sidebar li{margin:10px 0}'
        '.arc-application-sidebar a[aria-current=page]{font-weight:600}.arc-application-content{min-width:0;padding:24px}\n')
    return {'pages': records, 'menu': menu, 'shell': copy.deepcopy(manifest['shell']),
            'runtime': RUNTIME_PATH, 'stylesheet': CSS_PATH, 'runtime_read_only': True,
            'diagnostics': copy.deepcopy(manifest.get('diagnostics', [])),
            'state_api': ['usePageState', 'PageStateView', 'openState', 'closeState']}
