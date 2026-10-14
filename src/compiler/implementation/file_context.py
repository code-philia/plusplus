"""Direct requirement ownership, all-shared discovery, and request-scoped file loading."""
import copy
import json
from pathlib import Path
from compiler.backend_design.context import terms
from compiler.frontend_design.validation import ui_index
from compiler.lowering.types import refs
from .patcher import read_source, sha256

SYSTEM_FILES = {
    'backend/src/db/seed.ts': ('SYSTEM.SEED', 'Single cumulative startup reset and fixture insertion module'),
    'backend/src/db/initialize.ts': ('SYSTEM.DB_SCHEMA', 'Table creation statements and initializeDatabase'),
    'backend/src/db/client.ts': ('SYSTEM.DB_CLIENT', 'Database connection and repository binding'),
    'backend/src/app.ts': ('SYSTEM.SERVER_ROUTES', 'Express app and route registration'),
    'backend/src/server.ts': ('SYSTEM.SERVER_START', 'Startup reset/seed and HTTP server lifecycle'),
    'backend/src/types/contracts.ts': ('SYSTEM.BACKEND_TYPES', 'Backend exported DTO types'),
    'frontend/src/App.tsx': ('SYSTEM.APP', 'React app/page composition'),
    'frontend/src/main.tsx': ('SYSTEM.FRONTEND_ENTRY', 'React mount entrypoint'),
    'frontend/src/types/contracts.ts': ('SYSTEM.FRONTEND_TYPES', 'Frontend exported DTO types'),
    'frontend/src/index.css': ('SYSTEM.STYLES', 'Shared application styles'),
}


class FileContext:
    def __init__(self, root, requirements, database, backend, frontend, backend_plan, frontend_plan, database_plan):
        self.root = Path(root).resolve()
        self.requirements, self.database, self.backend, self.frontend = requirements, database, backend, frontend
        self.catalog = {}
        def add(path, symbol, reqs=(), shared=False, spec='', kind='source', contracts=None):
            if not path:
                return
            record = self.catalog.setdefault(path, {'file_path': path, 'symbol_ids': [], 'reqs': [], 'shared': False,
                                                    'summaries': [], 'kind': kind, 'contracts': []})
            record['symbol_ids'] = sorted(set(record['symbol_ids']) | {symbol})
            record['reqs'] = sorted(set(record['reqs']) | set(reqs))
            record['shared'] |= shared or len(record['reqs']) > 1
            if spec:
                record['summaries'].append(spec)
            if contracts is not None:
                record['contracts'].append(copy.deepcopy(contracts))
        for row in backend_plan['modules']:
            source = next(item for item in backend['modules'] if item['id'] == row['id'])
            add(row['file_path'], row['id'], source['reqs'], source.get('shared', False), source['spec'], contracts=source)
        nodes, _ = ui_index(frontend['ui'])
        for row in frontend_plan['ui']:
            source = nodes[row['id']]
            add(row['file_path'], row['id'], source['reqs'], len(source['reqs']) > 1,
                source['name'] + ': ' + source['description'], contracts={key: source[key] for key in ('id','name','kind','reqs','description','data','events')})
        for group in ('data', 'handlers', 'events'):
            source_by_id = {row['id']: row for row in frontend[group]}
            for row in frontend_plan[group]:
                source = source_by_id[row['id']]
                paths = [row['file_path']] if 'file_path' in row else [row['component_file_path'], row['handler_file_path']]
                for path in paths:
                    add(path, row['id'], source['reqs'], len(source['reqs']) > 1,
                        source.get('spec', source.get('name', row['id'])), contracts=source)
        for row in database_plan.get('shared_modules', []):
            add(row['file_path'], row['id'], shared=True, spec=row['name'])
        for path, (key, summary) in SYSTEM_FILES.items():
            if (self.root / path).is_file():
                add(path, key, shared=True, spec=summary)
        for row in database_plan.get('tables', []):
            add(row['file_path'], 'TABLE.' + row['name'], row['reqs'], True, row['name'])
        # Existing project files remain discoverable; source can be loaded for composition/helpers.
        for directory in ('backend/src', 'frontend/src', 'shared/src', 'tests/support'):
            base = self.root / directory
            if base.is_dir():
                for path in sorted(base.rglob('*')):
                    if path.is_file() and path.suffix in {'.ts', '.tsx', '.js', '.jsx', '.css', '.sql'}:
                        relative = path.relative_to(self.root).as_posix()
                        if relative not in self.catalog:
                            add(relative, 'FILE.' + relative, shared=directory == 'tests/support', spec=path.stem,
                                kind='test_support' if directory == 'tests/support' else 'source')
        for directory in ('backend', 'frontend', 'shared', 'tests'):
            base = self.root / directory
            for pattern in ('package.json', 'tsconfig*.json', '*config.ts', '*config.js'):
                for path in sorted(base.glob(pattern)):
                    add(path.relative_to(self.root).as_posix(), 'CONFIG.' + path.name, spec=path.name, kind='configuration')
        if (self.root / 'package.json').is_file():
            add('package.json', 'CONFIG.WORKSPACE', spec='Workspace scripts and dependencies', kind='configuration')

    def direct_files(self, rid):
        return {path for path, row in self.catalog.items() if rid in row['reqs'] and row['kind'] == 'source'}

    def summaries(self, records):
        return [{**{key: copy.deepcopy(row[key]) for key in ('file_path', 'symbol_ids', 'reqs', 'shared', 'kind')},
                 'summaries': list(dict.fromkeys(row['summaries']))[:4],
                 'symbol_count': len(row['symbol_ids'])} for row in records]

    def discover(self, rid, k=12):
        query = terms({key: self.requirements['nodes'][rid].get(key) for key in ('name','description','scenarios')})
        current_access = self.database.get('access', {}).get(rid, {})
        targets = set(current_access.get('read', [])) | set(current_access.get('write', []))
        entities = {key.split('.')[0] for key in targets}
        shared, scored = [], []
        for row in self.catalog.values():
            if row['shared']:
                shared.append(row)
                continue
            if rid in row['reqs']:
                continue
            score = len(query & terms(row['summaries']))
            for other_rid in row['reqs']:
                access = self.database.get('access', {}).get(other_rid, {})
                other = set(access.get('read', [])) | set(access.get('write', []))
                score = max(score, 20 * len(targets & other) + 5 * len(entities & {key.split('.')[0] for key in other}))
            if score:
                scored.append((score, row))
        scored.sort(key=lambda pair: (-pair[0], pair[1]['file_path']))
        return self.summaries(shared), self.summaries([row for _, row in scored[:k]])

    def resolve_requests(self, requests, test_targets):
        if len(requests) != len(set(requests)):
            raise ValueError('Duplicate request_files')
        by_symbol = {}
        for path, row in self.catalog.items():
            for key in row['symbol_ids']:
                by_symbol.setdefault(key, set()).add(path)
        result = set()
        for value in requests:
            paths = {value} if value in self.catalog or value in test_targets else by_symbol.get(value)
            if paths is None:
                raise ValueError(f'Unknown source path/symbol: {value}')
            result.update(paths)
        return result

    def build(self, rid, phase, loaded, test_targets, evidence):
        direct = self.direct_files(rid)
        source_paths = direct | loaded
        editable, readonly = {}, {}
        for path in sorted(source_paths | set(test_targets)):
            row = self.catalog.get(path)
            is_test = path in test_targets
            writable = (is_test if phase == 'test_generation' else
                        not is_test and row is not None and row['kind'] == 'source')
            if phase in {'backend_implementation', 'frontend_implementation'}:
                layer = phase.split('_')[0]
                writable = writable and (path.startswith(layer + '/') or path.startswith('shared/'))
            if phase == 'test_generation' and row and row['kind'] == 'test_support':
                writable = False
            text = read_source(self.root, path)
            record = {'content': text, 'sha256': sha256(text)}
            (editable if writable else readonly)[path] = record
        shared, related = self.discover(rid)
        requirement = copy.deepcopy(self.requirements['nodes'][rid])
        requirement.pop('visual_references', None)
        requirement.pop('children_ids', None)
        # Pull cached analyses into the requirement's original pages, not a parallel field.
        from utils.jsonio import read_json
        page_states = read_json(self.root / '.arc/design/frontend/page_states.json', [])
        for page in requirement.get('pages', []):
            state = next((row for row in page_states if row['name'] == page['name']), None)
            records = state.get('references', []) if state else []
            reference = str(page.get('reference', '')).replace('\\', '/')
            record = next((row for row in records if row['reference'] == reference), records[0] if records and not reference else {})
            page['analysis'] = record.get('analysis')
        constraints, parent = [], requirement.get('parent_id')
        seen = set()
        while parent in self.requirements['nodes'] and parent not in seen:
            seen.add(parent)
            node = self.requirements['nodes'][parent]
            constraints.append({key: node.get(key) for key in ('id','name','description')})
            parent = node.get('parent_id')
        requirement['ancestor_constraints'] = list(reversed(constraints))
        contracts = [copy.deepcopy(contract) for path in sorted(source_paths) if path in self.catalog
                     for contract in self.catalog[path]['contracts'] if rid in contract.get('reqs', []) or path in loaded]
        wanted_types = set()
        definitions = {row['name']: row for row in self.backend['types'] + self.frontend['types']}
        for contract in contracts:
            for field in ('input', 'output', 'payload', 'response', 'schema'):
                if field in contract:
                    wanted_types.update(refs(contract[field]))
        pending = list(wanted_types)
        while pending:
            name = pending.pop()
            if name in definitions:
                for ref in refs(definitions[name]['schema']) - wanted_types:
                    wanted_types.add(ref); pending.append(ref)
        data_access = self.database.get('access', {}).get(rid, {'read': [], 'write': []})
        return {'current_task': {'requirement': requirement, 'phase': phase, 'data_access': data_access},
                'current_contracts': contracts, 'types': [definitions[key] for key in sorted(wanted_types) if key in definitions],
                'editable_files': editable, 'read_only_files': readonly,
                'shared_catalog': shared, 'related_catalog': related,
                'test_targets': test_targets, 'gate_results': evidence,
                'always_loaded_files': sorted(direct)}
