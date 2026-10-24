"""Requirement/file/symbol indexes and read-only discovery queries."""
from __future__ import annotations
import copy
from pathlib import Path
from typing import Any
from types import MappingProxyType
from compiler.ir.backend import BackendIR
from compiler.ir.database import ERModelIR
from compiler.ir.frontend import FrontendIR
from compiler.context.retrieval import terms, overlap
from compiler.frontend_design.validation import ui_index
from compiler.preprocessing.queries import ancestor_ids

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


class FileCatalog:
    def __init__(
        self, root: str | Path, requirements: dict[str, Any], database: ERModelIR,
        backend: BackendIR, frontend: FrontendIR, backend_plan: dict[str, Any],
        frontend_plan: dict[str, Any], database_plan: dict[str, Any],
    ) -> None:
        self.root = Path(root).resolve()
        self.requirements, self.database, self.backend, self.frontend = requirements, database, backend, frontend
        self._records = {}
        self.recovery = {}
        def add(path, symbol, reqs=(), shared=False, spec='', kind='source', contracts=None):
            if not path:
                return
            record = self._records.setdefault(path, {'file_path': path, 'symbol_ids': [], 'reqs': [], 'shared': False,
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
        for directory in ('backend/src', 'frontend/src', 'shared/src', 'tests/support'):
            base = self.root / directory
            if base.is_dir():
                for path in sorted(base.rglob('*')):
                    if path.is_file() and path.suffix in {'.ts', '.tsx', '.js', '.jsx', '.css', '.sql'}:
                        relative = path.relative_to(self.root).as_posix()
                        if relative not in self._records:
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
        return {path for path, row in self._records.items() if row['kind'] == 'source'
                and any(rid in contract.get('reqs', []) for contract in row['contracts'])}

    def summaries(self, records):
        return [{**{key: copy.deepcopy(row[key]) for key in ('file_path', 'symbol_ids', 'reqs', 'shared', 'kind')},
                 'summaries': [text if len(text) <= 400 else text[:397] + '...'
                               for text in list(dict.fromkeys(row['summaries']))[:4]],
                 'symbol_count': len(row['symbol_ids'])} for row in records]

    def discover(self, rid, k=12, *, include=None, exclude=()):
        query = terms({key: self.requirements['nodes'][rid].get(key) for key in ('name','description','scenarios')})
        current_access = self.database.get('access', {}).get(rid, {})
        targets = set(current_access.get('read', [])) | set(current_access.get('write', []))
        entities = {key.split('.')[0] for key in targets}
        shared, scored = [], []
        for row in self._records.values():
            path = row['file_path']
            if path in exclude:
                continue
            if include is not None and not include(path):
                continue
            if row['shared']:
                shared.append(row)
                continue
            if rid in row['reqs']:
                continue
            score = overlap(query, row['summaries'])
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
        for path, row in self._records.items():
            for key in row['symbol_ids']:
                by_symbol.setdefault(key, set()).add(path)
        result = set()
        for value in requests:
            relative = value.replace('\\', '/')
            paths = {relative} if relative in self._records or relative in test_targets else by_symbol.get(value)
            if paths is None:
                raise ValueError(f'Unknown source path/symbol: {value}')
            result.update(paths)
        return result

    def failed_test_context(self, evidence):
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
                record = self._records.get(relative)
                if record and record['kind'] == 'acceptance_test':
                    failing_tests.add(relative)
                    regression_owners.update(record['reqs'])
        return failing_tests, regression_owners

    def ancestor_constraints(self, parent):
        nodes = self.requirements['nodes']
        return [{key: nodes[parent].get(key) for key in ('id', 'name', 'description')}
                for parent in ancestor_ids(nodes, parent)]

    @property
    def records(self):
        return MappingProxyType(self._records)

    def register(self, path, record):
        self._records[path] = copy.deepcopy(record)
