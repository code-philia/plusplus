"""Parent-grouped Express/TypeScript contract skeletons, without business bodies."""
import json
import re
from compiler.lowering.files import write_stage
from compiler.lowering.planning import group_for, group_directory, slug, relative_import
from compiler.lowering.types import contract, refs, type_file
from compiler.backend_design.validation import graph_errors, route_key


class BackendLoweringPass:
    def compile(self, requirements, backend, output_dir):
        if backend.get('ir_kind') != 'backend_design':
            raise ValueError('Expected backend_design IR')
        modules = backend['modules']
        if len({row['id'] for row in modules}) != len(modules):
            raise ValueError('Duplicate backend module IDs')
        if len({row['name'].casefold() for row in modules}) != len(modules):
            raise ValueError('Duplicate backend module names')
        errors = graph_errors(backend)
        if errors:
            raise ValueError('; '.join(errors))
        definitions = {row['name']: row['schema'] for row in backend['types']}
        types_path = 'backend/src/types/contracts.ts'
        files = {types_path: type_file(backend['types'])}
        records, routes = [], []
        seen_routes = set()
        for row in modules:
            if not re.fullmatch(r'[A-Z][A-Za-z0-9]*', row['name']):
                raise ValueError(f'Invalid module export name: {row["name"]}')
            group = group_for(row['reqs'], requirements)
            layer = {'API': 'api', 'FUNC': 'functions', 'DB': 'db'}[row['kind']]
            path = f'backend/src/modules/{group_directory(group, requirements)}/{layer}/{slug(row["name"])}-{slug(row["id"])}.ts'
            inp, out = (row['payload'], row['response']) if row['kind'] == 'API' else (row['input'], row['output'])
            needed = sorted(refs(inp) | refs(out))
            imports = ('import type * as Contracts from ' + json.dumps(relative_import(path, types_path)) + ';\n') if needed else ''
            input_name, output_name = row['name'] + 'Input', row['name'] + 'Output'
            files[path] = ('// Generated contract skeleton.\n' + imports
                + f'export type {input_name} = {contract(inp, definitions, namespace="Contracts.")};\n'
                + f'export type {output_name} = {contract(out, definitions, namespace="Contracts.")};\n'
                + f'export async function {row["name"]}(_input: {input_name}): Promise<{output_name}> {{\n'
                + '  throw new Error("Not implemented");\n}\n')
            records.append({'id': row['id'], 'name': row['name'], 'kind': row['kind'], 'reqs': row['reqs'],
                            'group_requirement_id': group, 'file_path': path, 'export': row['name'],
                            'input_type': input_name, 'output_type': output_name, 'callees': row['callees']})
            if row['kind'] == 'API':
                key = route_key(row)
                if key in seen_routes or row['method'] not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}:
                    raise ValueError(f'Duplicate or invalid route: {key}')
                seen_routes.add(key)
                routes.append({'module_id': row['id'], 'method': row['method'], 'path': row['path'], 'file_path': path})
        app_path = 'backend/src/app.ts'
        # The route contracts are registered, but no business flow is invoked yet.
        route_lines = [f'  app.{row["method"].lower()}({json.dumps(row["path"])}, (_request, response) => {{\n'
                       + f'    response.status(501).json({{ error: "Not implemented", module_id: {json.dumps(row["module_id"])} }});\n  }});'
                       for row in routes]
        files[app_path] = ('import express from "express";\n\nexport function createApp() {\n'
                          + '  const app = express();\n  app.use(express.json());\n'
                          + '\n'.join(route_lines) + '\n  return app;\n}\n')
        files['backend/src/server.ts'] = ('import { createApp } from "./app.js";\n'
            + 'const port = Number.parseInt(process.env.PORT ?? "3000", 10);\n'
            + 'export const server = createApp().listen(Number.isFinite(port) ? port : 3000);\n')
        plan = {'ir_kind': 'backend_lowering', 'strategy': 'deterministic_parent_groups',
                'entrypoint': 'backend/src/server.ts', 'modules': records, 'routes': routes,
                'types': [{'name': row['name'], 'file_path': types_path, 'export': row['name']} for row in backend['types']]}
        return write_stage(output_dir, 'backend', plan, files, bootstrap_files={'backend/src/app.ts', 'backend/src/server.ts'})
