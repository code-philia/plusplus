"""Leaf-only UI -> data -> interaction design with atomic requirement rollback."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from compiler.backend_design.context import database_context, module_index
from compiler.backend_design.validation import shape_errors
from compiler.preprocessing.traversal import children_by_id, postorder
from model.client import describe_model_error
from utils.jsonio import read_json, write_json_atomic
from utils.logging import SynchronousLog
from .context import (region, related_apis, state_context, handler_context, tree_summary,
                      select_types, expand_api_context)
from .contracts import PROMPTS, SCHEMAS, VISUAL_FIELDS, VISUAL_ANALYSIS_VERSION
from .delta import apply_ui, apply_data, apply_interaction, make_ui
from .validation import ui_index, validate_ir
from .visual import image_evidence


@dataclass
class FrontendDesignResult:
    ir: dict[str, Any]
    page_states: list[dict[str, Any]]
    node_states: dict[str, str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


class FrontendDesignPass:
    def __init__(self, model, artifact_root: Path, *, visual_model=None, reference_root: Path | None = None):
        self.model = model
        self.visual_model = visual_model
        self.artifact_root = Path(artifact_root)
        self.root = self.artifact_root / 'design/frontend'
        self.log = SynchronousLog('FrontendDesignPass', workspace_root=self.artifact_root.parent)
        self.visual_log = SynchronousLog('VisualAnalysis', workspace_root=self.artifact_root.parent)
        self.reference_root = reference_root
        self.cache_path = self.artifact_root / 'visual_cache.json'
        self.visual_cache = read_json(self.cache_path, {})
        self.visual_failures = {}
        try:
            self.attempts = max(1, int(os.getenv('MODEL_ATTEMPT', '5')))
        except ValueError:
            self.attempts = 5
        self.sequence = 0
        if hasattr(model, 'set_usage_path'):
            model.set_usage_path(self.artifact_root / 'model_usage.jsonl')

    def compile(self, requirements, database, backend):
        if database.get('ir_kind') != 'er_model' or database.get('status') != 'RESOLVED':
            raise ValueError('Frontend design requires a RESOLVED er_model.json')
        if backend.get('ir_kind') != 'backend_design':
            raise ValueError('Frontend design requires backend_ir.json')
        ir = {'ir_kind': 'frontend_design', 'ui': make_ui('UI_APP', 'Application', 'app', 'Application root.'),
              'data': [], 'events': [], 'handlers': [], 'types': copy.deepcopy(backend['types'])}
        self.backend_type_names = {row['name'] for row in backend['types']}
        initial_errors = validate_ir(ir, backend)
        if initial_errors:
            raise ValueError('Invalid imported backend types: ' + '; '.join(initial_errors))
        page_states, states, warnings = [], {}, []
        children = children_by_id(requirements)
        order = [rid for rid in postorder(requirements) if not children[rid]]
        for rid in order:
            self.log.info('Leaf design: RUNNING', node_id=rid)
            trial, trial_pages = copy.deepcopy(ir), copy.deepcopy(page_states)
            try:
                requirement = self._requirement(requirements['nodes'][rid], requirements['nodes'])
                if not requirement.get('pages'):
                    states[rid] = 'NO_FRONTEND'
                    self.log.info('No frontend page; skipping leaf.', node_id=rid)
                    continue
                evidence = {}
                for page in requirement['pages']:
                    reference = page.get('reference')
                    if reference:
                        try:
                            if reference in self.visual_failures:
                                raise ValueError(self.visual_failures[reference])
                            record = self._analyze(page['name'], reference, rid)
                            evidence[reference] = record
                            page['analysis'] = record['analysis']
                        except Exception as exc:
                            self.visual_failures[reference] = str(exc)
                            page['analysis'] = None
                            self.visual_log.warning(f'Skipping {reference}: {exc}', node_id=rid)
                            warnings.append(f'{rid}: visual analysis unavailable for {reference}; using requirement evidence')
                    else:
                        known = next((row for row in trial_pages if row['name'] == page['name']), None)
                        page['analysis'] = self._page_analysis(known)
                missing_page = any(not any(row['name'] == page['name'] for row in page_states)
                                   for page in requirement['pages'])
                data_access = database_context(database, rid)
                for page in requirement['pages']:
                    state = next((row for row in trial_pages if row['name'] == page['name']), None)
                    new_page = state is None
                    reference = page.get('reference')
                    record = evidence.get(reference)
                    if new_page:
                        nodes, _ = ui_index(trial['ui'])
                        key = f'UI_{len(nodes):06d}'
                        trial['ui']['children'].append(make_ui(key, page['name'], 'page', f'Page: {page["name"]}.'))
                        state = {'name': page['name'], 'ui_id': key, 'references': []}
                        trial_pages.append(state)
                    if not reference:
                        page['analysis'] = self._page_analysis(state)
                    parsed = not reference or not record or any(row['reference'] == reference and row['sha256'] == record['sha256']
                                                  for row in state['references'])
                    mode = 'new' if new_page else 'bind' if parsed else 'extend'
                    subtree = next(row for row in trial['ui']['children'] if row['id'] == state['ui_id'])
                    payload = {'current_task': {'requirement': requirement, 'data_access': data_access},
                               'current_page': {'name': page['name'], 'reference': reference, 'ui_id': state['ui_id']},
                               'page_mode': mode, 'selected_ui_tree': copy.deepcopy(subtree),
                               'loaded_ui_ids': list(ui_index(subtree)[0])}
                    if missing_page:
                        payload['ui_tree_summary'] = tree_summary(trial['ui'])
                    if mode == 'extend' and state['references']:
                        baseline = state['references'][0]
                        payload['baseline_page_states'] = {'name': state['name'], 'reference': baseline['reference'],
                                                           'analysis': self._page_analysis(state)}
                    trial = self._design('ui', rid, trial, payload, backend)
                    if record:
                        old = next((index for index, row in enumerate(state['references']) if row['reference'] == reference), None)
                        if old is None:
                            state['references'].append({key: record[key] for key in ('reference', 'sha256')})
                        else:
                            state['references'][old] = {key: record[key] for key in ('reference', 'sha256')}
                names = {page['name'] for page in requirement['pages']}
                pages, loaded = region(trial, names)
                apis = related_apis(backend, requirement, database)
                # Existing UI events need their exact direct API contracts available for reuse.
                handler_ids = {row['handler'] for row in trial['events'] if row['ui'] in loaded}
                api_ids = {key for row in trial['handlers'] if row['id'] in handler_ids for key in row['apis']}
                known_api_ids = {row['id'] for row in apis}
                apis.extend(copy.deepcopy(row) for row in backend['modules']
                            if row['kind'] == 'API' and row['id'] in api_ids - known_api_ids)
                payload = {'current_task': {'requirement': requirement, 'data_access': data_access},
                           'selected_ui_tree': pages, 'app': {key: trial['ui'][key] for key in ('id', 'name', 'kind')},
                           'loaded_ui_ids': sorted(loaded), 'api_contracts': apis,
                           'api_index': module_index([row for row in backend['modules'] if row['kind'] == 'API']),
                           'existing_data': state_context(trial, loaded),
                           'type_name_index': [row['name'] for row in trial['types']]}
                payload['existing_types'] = self._types(trial, apis, payload['existing_data'])
                trial = self._design('data', rid, trial, payload, backend)
                payload['selected_ui_tree'], loaded = region(trial, names)
                current_nodes, _ = ui_index(trial['ui'])
                payload['event_ui_candidates'] = [
                    {'id': key, 'name': current_nodes[key]['name'], 'kind': current_nodes[key]['kind'],
                     'description': current_nodes[key]['description']}
                    for key in sorted(loaded)]
                payload['existing_data'] = state_context(trial, loaded)
                payload['reuse_handlers'], payload['existing_events'] = handler_context(
                    trial, loaded, payload['existing_data'], apis, requirement)
                payload['handler_name_index'] = [row['name'] for row in trial['handlers']]
                payload['existing_types'] = self._types(trial, apis, payload['existing_data'], payload['reuse_handlers'])
                payload['type_name_index'] = [row['name'] for row in trial['types']]
                trial = self._design('interaction', rid, trial, payload, backend)
                self._save(trial, trial_pages)
                ir, page_states = trial, trial_pages
                states[rid] = 'DESIGNED'
                self.log.info('Leaf design: COMPLETED', node_id=rid)
            except Exception as exc:
                states[rid] = 'SKIPPED'
                warnings.append(f'{rid}: rolled back frontend leaf design: {exc}')
                self.log.warning(warnings[-1], node_id=rid)
        self._save(ir, page_states)
        write_json_atomic(self.root / 'report.json', {
            'status': 'GENERATED_WITH_WARNINGS' if warnings else 'GENERATED',
            'node_states': states, 'leaf_order': order, 'warnings': warnings, 'errors': [],
            'model_attempt': self.attempts, 'model_calls': self.sequence})
        return FrontendDesignResult(ir, page_states, states, warnings=warnings)

    @staticmethod
    def _requirement(node, nodes):
        result = {key: copy.deepcopy(node[key]) for key in ('id', 'name', 'description', 'scenarios', 'pages') if key in node}
        for page in result.get('pages', []):
            if page.get('reference'):
                page['reference'] = page['reference'].replace('\\', '/')
        ancestors, seen = [], set()
        parent = node.get('parent_id')
        while parent in nodes and parent not in seen:
            seen.add(parent)
            ancestors.append({key: nodes[parent].get(key, '') for key in ('id', 'name', 'description')})
            parent = nodes[parent].get('parent_id')
        result['ancestor_constraints'] = list(reversed(ancestors))
        return result

    def _types(self, ir, apis, data, handlers=()):
        roots = [row['schema'] for row in data]
        roots.extend(row[key] for row in handlers for key in ('input', 'output'))
        roots.extend(row['schema'] for row in ir['types'] if row['name'] not in self.backend_type_names)
        wanted = select_types(ir, apis, roots)
        wanted.update(row['name'] for row in ir['types'] if row['name'] not in self.backend_type_names)
        return [copy.deepcopy(row) for row in ir['types'] if row['name'] in wanted]

    def _design(self, phase, rid, base, payload, backend):
        apply = {'ui': apply_ui, 'data': apply_data, 'interaction': apply_interaction}[phase]
        def accept(output):
            if phase == 'interaction':
                fresh = expand_api_context(backend, payload, output)
                if fresh:
                    payload['existing_types'] = self._types(base, payload['api_contracts'],
                                                          payload['existing_data'], payload['reuse_handlers'])
                    raise ValueError(f'Loaded existing APIs {fresh} from api_index into api_contracts with their types. '
                                     'Review them before reporting a missing capability or returning the corrected delta.')
            candidate = apply(base, output, rid, payload)
            errors = validate_ir(candidate, backend)
            if errors:
                raise ValueError('; '.join(errors))
            return candidate
        return self._invoke(phase, rid, payload, accept)

    def _invoke(self, phase, rid, payload, accept, image_data_url=None):
        feedback, previous = [], None
        for attempt in range(1, self.attempts + 1):
            logger = self.visual_log if phase == 'visual' else self.log
            logger.info(f'{phase} design attempt {attempt}/{self.attempts}', node_id=rid)
            request = copy.deepcopy(payload)
            if feedback:
                request['validation_feedback'], request['previous_output'] = feedback, previous
            self.sequence += 1
            try:
                kwargs = {'schema_name': 'frontend_design_' + phase, 'instructions': PROMPTS[phase],
                          'input_payload': request, 'output_schema': SCHEMAS[phase]}
                if phase == 'visual':
                    if self.visual_model is None:
                        from model.vision import VisualModel
                        self.visual_model = VisualModel.from_env()
                    if hasattr(self.visual_model, 'set_usage_path'):
                        self.visual_model.set_usage_path(self.artifact_root / 'model_usage.jsonl')
                    output = self.visual_model.generate_visual_json(**kwargs, image_data_url=image_data_url)
                else:
                    output = self.model.generate_json(**kwargs)
                previous = output
                feedback = shape_errors(output, SCHEMAS[phase])
                if not feedback:
                    result = accept(output)
                    return result
            except Exception as exc:
                feedback = [describe_model_error(exc)]
            logger.warning(f'{phase} validation rejected: ' + '; '.join(feedback), node_id=rid)
        raise ValueError(f'{phase}: exhausted {self.attempts} attempts: ' + '; '.join(feedback))

    def _analyze(self, name, reference, rid):
        digest, image = image_evidence(reference, self.reference_root)
        cached = self.visual_cache.get(reference)
        if (isinstance(cached, dict) and cached.get('sha256') == digest
                and cached.get('analysis_version') == VISUAL_ANALYSIS_VERSION
                and isinstance(cached.get('analysis'), str) and cached['analysis'].strip()):
            self.visual_log.info(f'Cache hit: {name} ({reference})', node_id=rid)
            return copy.deepcopy(cached)
        self.visual_log.info(f'Analyzing: {name} ({reference})', node_id=rid)
        def accept(output):
            for field in VISUAL_FIELDS:
                if not output[field] or any(not item.strip() for item in output[field]):
                    raise ValueError(f'Visual analysis must include nonempty {field}')
            return output
        details = self._invoke('visual', rid, {'current_task': {'page': {'name': name, 'reference': reference}}}, accept, image)
        analysis = '\n\n'.join(field + ':\n' + '\n'.join('- ' + item for item in details[field])
                               for field in VISUAL_FIELDS)
        record = {'reference': reference, 'sha256': digest, 'analysis_version': VISUAL_ANALYSIS_VERSION,
                  'analysis': analysis}
        self.visual_cache[reference] = record
        write_json_atomic(self.cache_path, self.visual_cache)
        self.visual_log.info(f'Analysis completed: {name} ({reference})', node_id=rid)
        return copy.deepcopy(record)

    def _save(self, ir, page_states):
        write_json_atomic(self.root / 'frontend_ir.json', ir)
        write_json_atomic(self.root / 'page_states.json', page_states)

    def _page_analysis(self, state):
        references = state.get('references', []) if state else []
        return self.visual_cache.get(references[0]['reference'], {}).get('analysis') if references else None
