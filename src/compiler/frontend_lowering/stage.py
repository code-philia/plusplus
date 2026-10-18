"""Bounded-depth region components and typed handler skeletons for React."""
import json
import re
from compiler.frontend_design.validation import ui_index
from compiler.lowering.files import write_stage
from compiler.lowering.planning import group_for, group_directory, unique_name, portable_stem, relative_import
from compiler.lowering.types import contract, refs, type_file


class FrontendLoweringPass:
    def __init__(self, *, component_depth=2, min_nodes=4):
        if component_depth < 1 or min_nodes < 2:
            raise ValueError('component_depth >= 1 and min_nodes >= 2 required')
        self.component_depth, self.min_nodes = component_depth, min_nodes

    def compile(self, requirements, frontend, output_dir, *, dry_run=False):
        if frontend.get('ir_kind') != 'frontend_design':
            raise ValueError('Expected frontend_design IR')
        nodes, parents = ui_index(frontend['ui'])
        definitions = {row['name']: row['schema'] for row in frontend['types']}
        types_path = 'frontend/src/types/contracts.ts'
        files = {types_path: type_file(frontend['types'])}
        components, ownership, child_components = [], {}, {}
        component_by_ui = {}
        export_names = {'App', 'AppState', 'AppProps', 'Contracts'}
        def subtree_reqs(row):
            return set(row['reqs']) | {rid for child in row['children'] for rid in subtree_reqs(child)}
        def size(row):
            return 1 + sum(size(child) for child in row['children'])
        def select(row, page, depth, owner=None):
            extract = row['kind'] == 'page' or (depth <= self.component_depth and size(row) >= self.min_nodes
                       and row['kind'] in {'region', 'form', 'list', 'dialog', 'navigation'})
            if extract:
                reqs = sorted(subtree_reqs(row))
                group = group_for(reqs, requirements)
                export = unique_name(row['name'], export_names, qualifier=page['name'],
                                     fallback='Page' if row['kind'] == 'page' else 'Component')
                while { (export + 'State').casefold(), (export + 'Props').casefold() } & {name.casefold() for name in export_names}:
                    export = unique_name(row['name'], export_names, qualifier=page['name'])
                export_names.update({export + 'State', export + 'Props'})
                directory = "pages" if row["kind"] == "page" else "components"
                path = f'frontend/src/features/{group_directory(group, requirements)}/{directory}/{portable_stem(export)}.tsx'
                record = {'ui_id': row['id'], 'name': row['name'], 'export': export, 'file_path': path,
                          'reqs': reqs, 'group_requirement_id': group, 'depth_from_page': depth,
                          'reason': 'page' if row['kind'] == 'page' else 'shallow_structural_region'}
                components.append(record)
                component_by_ui[row['id']] = record
                child_components[row['id']] = []
                if owner:
                    child_components[owner].append(row['id'])
                owner = row['id']
            ownership[row['id']] = owner
            for child in row['children']:
                select(child, page, depth + 1, owner)
        for page in frontend['ui']['children']:
            select(page, page, 0)
        app_path = 'frontend/src/App.tsx'
        ownership[frontend['ui']['id']] = frontend['ui']['id']
        component_by_ui[frontend['ui']['id']] = {'file_path': app_path, 'export': 'App'}
        ui_records = []
        for row in nodes.values():
            owner = ownership[row['id']]
            ui_records.append({'id': row['id'], 'component_ui_id': owner,
                               'file_path': component_by_ui[owner]['file_path'], 'standalone': row['id'] == owner})
        data_records = []
        state_by_component = {}
        state_properties = {}
        property_names = {}
        for row in frontend['data']:
            if row['owner'] not in ownership:
                raise ValueError(f'Unknown state owner: {row["owner"]}')
            owner = ownership[row['owner']]
            state_by_component.setdefault(owner, []).append(row)
            used = property_names.setdefault(owner, set())
            prop = row['name']
            number = 2
            while prop in used:
                prop = row['name'] + '_' + str(number)
                number += 1
            used.add(prop)
            state_properties[row['id']] = prop
            data_records.append({'id': row['id'], 'owner_ui_id': row['owner'], 'component_ui_id': owner,
                                 'file_path': component_by_ui[owner]['file_path'], 'export': component_by_ui[owner]['export'] + 'State',
                                 'property': prop})
        def state_contract(owner, path):
            states = state_by_component.get(owner, [])
            needed = sorted(set().union(*(refs(row['schema']) for row in states))) if states else []
            imports = ('import type * as Contracts from ' + json.dumps(relative_import(path, types_path, '')) + ';\n') if needed else ''
            fields = ' '.join(json.dumps(state_properties[row['id']]) + ': ' + contract(row['schema'], definitions, False, namespace='Contracts.') + ';' for row in states)
            return imports + 'export type ' + component_by_ui[owner]['export'] + 'State = { ' + fields + ' };\n'
        for row in components:
            path, key = row['file_path'], row['ui_id']
            children = [component_by_ui[child] for child in child_components[key]]
            imports = ''.join('import { ' + child['export'] + ' } from ' + json.dumps(relative_import(path, child['file_path'], '')) + ';\n' for child in children)
            body = '\n'.join('    <' + child['export'] + ' />' for child in children)
            files[path] = (imports + state_contract(key, path) + f'export type {row["export"]}Props = {{}};\n'
                + f'export function {row["export"]}(_props: {row["export"]}Props) {{\n'
                + f'  return <section data-ui-id={json.dumps(key)}>\n{body}\n  </section>;\n}}\n')
        pages = [component_by_ui[page['id']] for page in frontend['ui']['children']]
        imports = ''.join('import { ' + row['export'] + ' } from ' + json.dumps(relative_import(app_path, row['file_path'], '')) + ';\n' for row in pages)
        files[app_path] = (imports + state_contract(frontend['ui']['id'], app_path)
                          + 'export function App() {\n  return <>\n'
                          + '\n'.join('    <' + row['export'] + ' />' for row in pages) + '\n  </>;\n}\nexport default App;\n')
        files['frontend/src/main.tsx'] = ('import "./index.css";\nimport { createRoot } from "react-dom/client";\nimport App from "./App";\n'
            + 'const root = document.getElementById("root");\nif (root) createRoot(root).render(<App />);\n')
        handlers = []
        for row in frontend['handlers']:
            if not re.fullmatch(r'[A-Z][A-Za-z0-9]*', row['name']):
                raise ValueError(f'Invalid handler export name: {row["name"]}')
            group = group_for(row['reqs'], requirements)
            path = f'frontend/src/features/{group_directory(group, requirements)}/handlers/{portable_stem(row["name"])}.ts'
            needed = sorted(refs(row['input']) | refs(row['output']))
            imports = ('import type * as Contracts from ' + json.dumps(relative_import(path, types_path, '')) + ';\n') if needed else ''
            files[path] = (imports + f'export type {row["name"]}Input = {contract(row["input"], definitions, namespace="Contracts.")};\n'
                + f'export type {row["name"]}Output = {contract(row["output"], definitions, namespace="Contracts.")};\n'
                + f'export async function {row["name"]}(_input: {row["name"]}Input): Promise<{row["name"]}Output> {{\n'
                + '  throw new Error("Not implemented");\n}\n')
            handlers.append({'id': row['id'], 'name': row['name'], 'reqs': row['reqs'], 'group_requirement_id': group,
                             'file_path': path, 'export': row['name'], 'input_type': row['name'] + 'Input',
                             'output_type': row['name'] + 'Output', 'read': row['read'], 'write': row['write'], 'apis': row['apis']})
        handler_paths = {row['id']: row['file_path'] for row in handlers}
        events = [{'id': row['id'], 'ui_id': row['ui'], 'handler_id': row['handler'],
                   'component_file_path': component_by_ui[ownership[row['ui']]]['file_path'],
                   'handler_file_path': handler_paths[row['handler']], 'wired': False} for row in frontend['events']]
        plan = {'ir_kind': 'frontend_lowering', 'strategy': 'shallow_structural_subtrees',
                'component_depth': self.component_depth, 'min_nodes': self.min_nodes,
                'entrypoint': 'frontend/src/main.tsx', 'app_file_path': app_path,
                'app': {'ui_id': frontend['ui']['id'], 'file_path': app_path, 'export': 'App'},
                'components': components, 'ui': ui_records, 'data': data_records, 'events': events,
                'handlers': handlers, 'types': [{'name': row['name'], 'file_path': types_path, 'export': row['name']} for row in frontend['types']]}
        if dry_run:
            return plan, files
        return write_stage(output_dir, 'frontend', plan, files, bootstrap_files={'frontend/src/App.tsx', 'frontend/src/main.tsx'})
