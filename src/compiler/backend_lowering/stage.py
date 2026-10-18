"""Parent-grouped Express/TypeScript contract skeletons, without business bodies."""
import json
import re
from compiler.lowering.files import write_stage
from compiler.lowering.planning import group_for, group_directory, portable_stem, relative_import
from compiler.lowering.types import contract, refs, type_file
from compiler.backend_design.validation import graph_errors, route_key


class BackendLoweringPass:
    def compile(self, requirements, backend, output_dir, *, dry_run=False):
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
            layer = {'API': 'controllers', 'FUNC': 'services', 'DB': 'repositories'}[row['kind']]
            directory = 'shared' if row['shared'] else 'features/' + group_directory(group, requirements)
            path = f'backend/src/{directory}/{layer}/{portable_stem(row["name"])}.ts'
            inp, out = (row['payload'], row['response']) if row['kind'] == 'API' else (row['input'], row['output'])
            needed = sorted(refs(inp) | refs(out))
            imports = ('import type * as Contracts from ' + json.dumps(relative_import(path, types_path)) + ';\n') if needed else ''
            input_name, output_name = row['name'] + 'Input', row['name'] + 'Output'
            files[path] = ('// Generated contract skeleton.\n' + imports
                + f'export type {input_name} = {contract(inp, definitions, namespace="Contracts.", payload=row["kind"] == "API")};\n'
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
        files[app_path] = ('import express from "express";\n'
                          + 'import { fileURLToPath } from "node:url";\n\nexport function createApp() {\n'
                          + '  const app = express();\n  app.use(express.json());\n'
                          + '  app.get("/__arc/health", (_request, response) => { response.json({ status: "ok" }); });\n'
                          + '\n'.join(route_lines)
                          + '\n  app.use(express.static(fileURLToPath(new URL("../../frontend/dist/", import.meta.url))));\n'
                          + '  app.use((request, response, next) => {\n'
                          + '    if (request.method !== "GET" || request.path.startsWith("/api")) { next(); return; }\n'
                          + '    response.sendFile(fileURLToPath(new URL("../../frontend/dist/index.html", import.meta.url)));\n'
                          + '  });\n  return app;\n}\n')
        files['backend/src/server.ts'] = ('import { createApp } from "./app.js";\n'
            + 'import { serverPort } from "./config/server.js";\n'
            + 'import { createDatabaseClient, bindDatabaseClient } from "./db/client.js";\n'
            + 'import { seedDatabase } from "./db/seed.js";\n'
            + 'import { initializeDatabase } from "./db/initialize.js";\n'
            + 'const client = createDatabaseClient();\n'
            + 'try { if (process.env.ARC_VALIDATION_PREPARED === "1") initializeDatabase(client.connection);\n'
            + 'else seedDatabase(client.connection); bindDatabaseClient(client); }\n'
            + 'catch (error) { client.close(); throw error; }\n'
            + 'const port = serverPort();\n'
            + 'export const server = createApp().listen(port, () => {\n'
            + '  const address = server.address();\n'
            + '  const actualPort = address && typeof address === "object" ? address.port : port;\n'
            + '  console.log(`Application ready: http://localhost:${actualPort}`);\n'
            + '  console.log(`Health: http://localhost:${actualPort}/__arc/health`);\n'
            + '});\n'
            + 'server.on("error", (error: NodeJS.ErrnoException) => {\n'
            + '  console.error(error.code === "EADDRINUSE" ? `Port ${port} is already in use` : error.message);\n'
            + '  client.close(); process.exitCode = 1;\n});\n'
            + 'server.on("close", () => client.close());\n'
            + 'process.once("SIGTERM", () => server.close());\nprocess.once("SIGINT", () => server.close());\n')
        plan = {'ir_kind': 'backend_lowering', 'strategy': 'deterministic_parent_groups',
                'entrypoint': 'backend/src/server.ts', 'modules': records, 'routes': routes,
                'types': [{'name': row['name'], 'file_path': types_path, 'export': row['name']} for row in backend['types']]}
        if dry_run:
            return plan, files
        return write_stage(output_dir, 'backend', plan, files, bootstrap_files={'backend/src/app.ts', 'backend/src/server.ts'})
