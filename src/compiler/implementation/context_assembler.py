"""Read selected source and assemble declared implementation evidence."""
from __future__ import annotations
import copy
import json
from agent.files import read_source
from compiler.ir.type_registry import TypeRegistry
from .context_types import ImplementationContext


class ContextAssembler:
    def __init__(self, files, policy):
        self.files, self.policy = files, policy
        self.root, self.requirements = files.root, files.requirements
        self.database, self.backend, self.frontend = files.database, files.backend, files.frontend

    def read(self, paths):
        return {path: {'content': read_source(self.root, path), 'exists': (self.root / path).is_file()}
                for path in sorted(paths)}

    def assemble(self, *, rid, phase, session, test_targets, evidence, records, writable_paths,
                 context_state, source_paths, visible_tests, direct, shared, related,
                 failing_tests, regression_owners) -> ImplementationContext:
        editable, readonly = {}, {}
        for path, record in records.items():
            section = 'editable_files' if path in writable_paths else 'read_only_files'
            record['context_location'] = section
            if path in writable_paths:
                editable[path] = record
            else:
                readonly[path] = record
        for result in context_state.get('last_operation', {}).get('results', []):
            if result['file'] in records:
                result['content_ref'] = records[result['file']]['context_location'] + '[' + result['file'] + ']'
        requirement = copy.deepcopy(self.requirements['nodes'][rid])
        requirement.pop('visual_references', None)
        requirement.pop('children_ids', None)
        from utils.jsonio import read_json
        page_states = read_json(self.root / '.arc/design/frontend/page_states.json', [])
        visual_cache = read_json(self.root / '.arc/visual_cache.json', {})
        file_index_exclusions = set(records)
        for page in requirement.get('pages', []):
            if not page.get('name'):
                matches = [row for row in page_states if any(
                    reference.get('image_id') == page.get('image_id')
                    if reference.get('image_id') else reference.get('reference') == page.get('reference')
                    for reference in row['references'])]
                if len(matches) == 1:
                    page['name'] = matches[0]['name']
            state = next((row for row in page_states if row['name'] == page['name']), None)
            page_references = state.get('references', []) if state else []
            reference = str(page.get('reference', '')).replace('\\', '/')
            if not reference and page_references:
                reference = page_references[0]['reference']
            page['analysis'] = visual_cache.get(reference, {}).get('analysis')
        requirement['ancestor_constraints'] = self.files.ancestor_constraints(requirement.get('parent_id'))
        contracts = [copy.deepcopy(contract) for path in sorted(source_paths) if path in self.files.records
                     for contract in self.files.records[path]['contracts'] if rid in contract.get('reqs', []) or path in session.active]
        if phase == 'frontend_implementation':
            needed_apis = {api for row in self.frontend['handlers'] if rid in row['reqs']
                           for api in row['apis']}
            contracts += [copy.deepcopy(row) for row in self.backend['modules']
                          if row['kind'] == 'API' and (rid in row['reqs'] or row['id'] in needed_apis)]
        contracts = list({json.dumps(row, sort_keys=True, ensure_ascii=False): row for row in contracts}.values())
        registry = TypeRegistry(self.backend['types'] + self.frontend['types'])
        wanted_types = registry.resolve(
            contract[field] for contract in contracts
            for field in ('input', 'output', 'payload', 'response', 'schema') if field in contract).names
        data_access = self.database.get('access', {}).get(rid, {'read': [], 'write': []})
        return {'current_task': {'requirement': requirement, 'phase': phase, 'data_access': data_access,
                                 **({'recovery': self.files.recovery[rid]} if rid in self.files.recovery else {})},
                'current_contracts': contracts, 'types': [registry[key] for key in sorted(wanted_types)],
                'editable_files': editable, 'read_only_files': readonly,
                'shared_catalog': shared, 'related_catalog': related,
                'test_targets': [path for path in test_targets if self.policy.phase_path(path, phase)],
                'gate_results': error_feedback(evidence),
                'failure_context': {'test_files': sorted(failing_tests),
                    **({'startup_files': [path for path in
                        ('backend/src/server.ts', 'backend/src/app.ts', 'backend/src/db/seed.ts',
                         'backend/src/db/initialize.ts', 'backend/src/db/client.ts') if path in self.files.records]}
                       if any(row.get('failure_kind') in {'application_startup', 'application_runtime'}
                              for row in evidence) else {}),
                    'regression_requirements': [copy.deepcopy(self.requirements['nodes'][key])
                        for key in sorted(regression_owners - {rid}) if key in self.requirements['nodes']]},
                'context_state': context_state,
                'file_index': [{'file': path, 'symbols': row['symbol_ids'], 'kind': row['kind']}
                               for path, row in sorted(self.files.records.items())
                               if path not in file_index_exclusions and self.policy.phase_path(path, phase)],
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
