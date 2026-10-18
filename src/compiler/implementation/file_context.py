"""Direct requirement ownership, all-shared discovery, and request-scoped file loading."""
import copy
import json
from pathlib import Path
from compiler.backend_design.context import terms
from compiler.frontend_design.validation import ui_index
from compiler.lowering.types import refs
from .patcher import read_source

SYSTEM_FILES = {
    'backend/src/db/seed.ts': ('SYSTEM.SEED', 'Single cumulative startup reset and fixture insertion module'),
    'backend/src/db/initialize.ts': ('SYSTEM.DB_SCHEMA', 'Table creation statements and initializeDatabase'),
    'backend/src/db/client.ts': ('SYSTEM.DB_CLIENT', 'Database connection and repository binding'),
    'backend/src/app.ts': ('SYSTEM.SERVER_ROUTES', 'Express app and route registration'),
    'backend/src/server.ts': ('SYSTEM.SERVER_START', 'Startup reset/seed and HTTP server lifecycle'),
    'backend/src/config/server.ts': ('SYSTEM.SERVER_CONFIG', 'Configured default port and PORT environment override'),
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
        self.recovery = {}
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
        for layer, plan in (('backend', backend_plan), ('frontend', frontend_plan)):
            for row in plan.get('recovery', []):
                rid = row['requirement_id']
                self.recovery.setdefault(rid, {})[layer] = copy.deepcopy(row)
                for path in row['file_paths']:
                    add(path, 'RECOVERY.' + rid + '.' + path, [rid], spec=row['reason'],
                        contracts={'reqs': [rid], 'recovery': True, 'reason': row['reason']})
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
        # Module/UI/data/event ownership supplies direct source. Table usage alone must
        # not preload shared schema/connection/seed infrastructure.
        return {path for path, row in self.catalog.items() if row['kind'] == 'source'
                and any(rid in contract.get('reqs', []) for contract in row['contracts'])}

    def summaries(self, records):
        return [{**{key: copy.deepcopy(row[key]) for key in ('file_path', 'symbol_ids', 'reqs', 'shared', 'kind')},
                 'summaries': [text if len(text) <= 400 else text[:397] + '...'
                               for text in list(dict.fromkeys(row['summaries']))[:4]],
                 'symbol_count': len(row['symbol_ids'])} for row in records]

    def discover(self, rid, k=12, *, phase=None, exclude=()):
        query = terms({key: self.requirements['nodes'][rid].get(key) for key in ('name','description','scenarios')})
        current_access = self.database.get('access', {}).get(rid, {})
        targets = set(current_access.get('read', [])) | set(current_access.get('write', []))
        entities = {key.split('.')[0] for key in targets}
        shared, scored = [], []
        for row in self.catalog.values():
            path = row['file_path']
            if path in exclude:
                continue
            if phase == 'backend_implementation' and not (path.startswith('backend/') or path.startswith('shared/')):
                continue
            if phase == 'frontend_implementation' and not (path.startswith('frontend/') or path.startswith('shared/')):
                continue
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
        by_symbol = {}
        for path, row in self.catalog.items():
            for key in row['symbol_ids']:
                by_symbol.setdefault(key, set()).add(path)
        result = set()
        for value in requests:
            relative = value.replace('\\', '/')
            paths = {relative} if relative in self.catalog or relative in test_targets else by_symbol.get(value)
            if paths is None:
                raise ValueError(f'Unknown source path/symbol: {value}')
            result.update(paths)
        return result

    @staticmethod
    def phase_path(path, phase):
        if phase == 'backend_implementation':
            return path.startswith(('backend/', 'shared/', 'tests/unit/', 'tests/integration/')) or path in {'tests/tdd/setup.ts', 'tests/tdd/vitest.config.ts'}
        if phase == 'frontend_implementation':
            return path.startswith(('frontend/', 'shared/', 'tests/e2e/')) or path == 'tests/tdd/playwright.config.ts'
        return True

    def build(self, rid, phase, session, test_targets, evidence):
        direct = self.direct_files(rid)
        source_paths = (direct | session.active) - session.released
        if phase == 'backend_implementation':
            source_paths = {path for path in source_paths
                            if path.startswith('backend/') or path.startswith('shared/')}
        elif phase == 'frontend_implementation':
            source_paths = {path for path in source_paths
                            if path.startswith('frontend/') or path.startswith('shared/')}
            recovered_backend = self.recovery.get(rid, {}).get('backend')
            if recovered_backend:
                source_paths.update(recovered_backend['file_paths'])
                source_paths.add('backend/src/app.ts')
        # Include failing regression tests even when owned by an earlier requirement.
        # Only known test files may be selected from untrusted runner output.
        failing_tests = set()
        regression_owners = set()
        for result in evidence:
            if result.get('status') == 'PASSED':
                continue
            failures = result.get('test_evidence', {}).get('failed_tests', [])
            candidates = [test.get('file') for test in failures]
            if not failures:
                candidates += result.get('test_files', [])
            for candidate in candidates:
                if not isinstance(candidate, str):
                    continue
                path = (self.root / candidate).resolve()
                if not path.is_relative_to(self.root):
                    continue
                relative = path.relative_to(self.root).as_posix()
                record = self.catalog.get(relative)
                if record and record['kind'] == 'acceptance_test':
                    failing_tests.add(relative)
                    regression_owners.update(record['reqs'])
        editable, readonly, records = {}, {}, {}
        visible_tests = {path for path in set(test_targets) | failing_tests if self.phase_path(path, phase)}
        writable_paths = set()
        for path in sorted(source_paths | visible_tests):
            row = self.catalog.get(path)
            is_test = path in test_targets or bool(row and row['kind'] == 'acceptance_test')
            writable = (path in test_targets if phase == 'test_generation' else
                        row is not None and (row['kind'] == 'source' or
                                             phase == 'repair' and is_test))
            if phase in {'backend_implementation', 'frontend_implementation'}:
                layer = phase.split('_')[0]
                writable = writable and (path.startswith(layer + '/') or path.startswith('shared/'))
            if phase == 'test_generation' and row and row['kind'] == 'test_support':
                writable = False
            text = read_source(self.root, path)
            records[path] = {'content': text, 'exists': (self.root / path).is_file()}
            if writable:
                writable_paths.add(path)
        records, context_state = session.select(records, visible_tests, direct)
        for path, record in records.items():
            section = 'editable_files' if path in writable_paths else 'read_only_files'
            record['context_location'] = section
            (editable if path in writable_paths else readonly)[path] = record
        for result in context_state.get('last_operation', {}).get('results', []):
            if result['file'] in records:
                result['content_ref'] = records[result['file']]['context_location'] + '[' + result['file'] + ']'
        source_paths &= records.keys()
        shared, related = self.discover(rid, phase=phase, exclude=source_paths | visible_tests)
        requirement = copy.deepcopy(self.requirements['nodes'][rid])
        requirement.pop('visual_references', None)
        requirement.pop('children_ids', None)
        # Pull cached analyses into the requirement's original pages, not a parallel field.
        from utils.jsonio import read_json
        page_states = read_json(self.root / '.arc/design/frontend/page_states.json', [])
        visual_cache = read_json(self.root / '.arc/visual_cache.json', {})
        for page in requirement.get('pages', []):
            state = next((row for row in page_states if row['name'] == page['name']), None)
            records = state.get('references', []) if state else []
            reference = str(page.get('reference', '')).replace('\\', '/')
            if not reference and records:
                reference = records[0]['reference']
            page['analysis'] = visual_cache.get(reference, {}).get('analysis')
        constraints, parent = [], requirement.get('parent_id')
        seen = set()
        while parent in self.requirements['nodes'] and parent not in seen:
            seen.add(parent)
            node = self.requirements['nodes'][parent]
            constraints.append({key: node.get(key) for key in ('id','name','description')})
            parent = node.get('parent_id')
        requirement['ancestor_constraints'] = list(reversed(constraints))
        contracts = [copy.deepcopy(contract) for path in sorted(source_paths) if path in self.catalog
                     for contract in self.catalog[path]['contracts'] if rid in contract.get('reqs', []) or path in session.active]
        if phase == 'frontend_implementation':
            # Frontend implementation receives backend API contracts, not backend source.
            needed_apis = {api for row in self.frontend['handlers'] if rid in row['reqs']
                           for api in row['apis']}
            contracts += [copy.deepcopy(row) for row in self.backend['modules']
                          if row['kind'] == 'API' and (rid in row['reqs'] or row['id'] in needed_apis)]
        contracts = list({json.dumps(row, sort_keys=True, ensure_ascii=False): row for row in contracts}.values())
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
        return {'current_task': {'requirement': requirement, 'phase': phase, 'data_access': data_access,
                                 **({'recovery': self.recovery[rid]} if rid in self.recovery else {})},
                'current_contracts': contracts, 'types': [definitions[key] for key in sorted(wanted_types) if key in definitions],
                'editable_files': editable, 'read_only_files': readonly,
                'shared_catalog': shared, 'related_catalog': related,
                'test_targets': [path for path in test_targets if self.phase_path(path, phase)],
                'gate_results': error_feedback(evidence),
                'failure_context': {'test_files': sorted(failing_tests),
                    **({'startup_files': [path for path in
                        ('backend/src/server.ts', 'backend/src/app.ts', 'backend/src/db/seed.ts',
                         'backend/src/db/initialize.ts', 'backend/src/db/client.ts') if path in self.catalog]}
                       if any(row.get('failure_kind') in {'application_startup', 'application_runtime'}
                              for row in evidence) else {}),
                    'regression_requirements': [copy.deepcopy(self.requirements['nodes'][key])
                        for key in sorted(regression_owners - {rid}) if key in self.requirements['nodes']]},
                'context_state': context_state,
                'file_index': [{'file': path, 'symbols': row['symbol_ids'], 'kind': row['kind']}
                               for path, row in sorted(self.catalog.items())
                               if path not in records and self.phase_path(path, phase)],
                'always_loaded_files': sorted(direct & source_paths)}


def error_feedback(evidence):
    """Send concrete errors for failed commands; omit verbose successful command output."""
    result = []
    for row in evidence:
        if row.get('status') == 'PASSED':
            continue
        item = {key: copy.deepcopy(row[key]) for key in
                ('status', 'failure_kind', 'command', 'exit_code', 'error', 'report_error', 'output',
                 'application_output', 'application_exit_code') if key in row}
        test = row.get('test_evidence')
        if test:
            item['test_evidence'] = {key: copy.deepcopy(test[key]) for key in
                ('collected', 'executed_files', 'failed_tests') if key in test}
            if not test.get('failed_tests'):
                item['test_evidence']['failures'] = test.get('failures', [])
        result.append(item)
    return result
