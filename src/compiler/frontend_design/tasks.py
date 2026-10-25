"""Transactional frontend subtask candidates; only the stage publishes a leaf."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from compiler.backend_design.context import database_context, module_index
from .context import region, related_apis, state_context, handler_context, tree_summary
from .delta import make_ui
from .validation import ui_index


@dataclass(frozen=True, slots=True)
class FrontendTaskInput:
    requirement_id: str
    requirement: dict[str, Any]
    candidate: dict[str, Any]
    page_states: list[dict[str, Any]]
    database: dict[str, Any]
    backend: dict[str, Any]
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FrontendTaskOutput:
    candidate: dict[str, Any]
    page_states: list[dict[str, Any]]
    requirement: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)


class PageBindingTask:
    def __init__(self, decide):
        self.decide = decide

    def run(self, requirement, page_states, tree):
        requirement = copy.deepcopy(requirement)
        pending = {}
        for page in requirement['pages']:
            if page.get('name'):
                continue
            matched = [state for state in page_states if any(
                reference.get('image_id') == page.get('image_id')
                if reference.get('image_id') else reference.get('reference') == page.get('reference')
                for reference in state['references'])]
            if len(matched) == 1:
                page['name'] = matched[0]['name']
            else:
                pending[page['image_id']] = page
        if not pending and (requirement['pages'] or not isinstance(requirement.get('description'), dict)):
            return requirement
        def accept(output):
            bindings = output['bindings']
            identities = [row['image_id'] for row in bindings]
            if len(identities) != len(set(identities)) or set(identities) != set(pending):
                raise ValueError('Bind every unassigned image exactly once; do not override explicit page identifiers')
            if any(not row['page'].strip() for row in bindings):
                raise ValueError('Inferred page identifiers must be nonempty')
            text_pages = output.get('text_pages', [])
            if any(not page.strip() for page in text_pages) or len(set(text_pages)) != len(text_pages):
                raise ValueError('Text-derived pages must have unique nonempty identifiers')
            return {'bindings': {row['image_id']: row['page'] for row in bindings}, 'text_pages': text_pages}
        bindings = self.decide({'current_task': {'requirement': requirement},
            'unassigned_images': list(pending.values()), 'ui_tree_summary': tree_summary(tree)}, accept)
        for identity, page in pending.items():
            page['name'] = bindings['bindings'][identity]
        existing = {page['name'] for page in requirement['pages']}
        requirement['pages'].extend({'name': name} for name in bindings['text_pages'] if name not in existing)
        return requirement


class UIDesignTask:
    def __init__(self, design, page_analysis):
        self.design, self.page_analysis = design, page_analysis

    def run(self, inputs: FrontendTaskInput) -> FrontendTaskOutput:
        rid, requirement = inputs.requirement_id, copy.deepcopy(inputs.requirement)
        trial, trial_pages = copy.deepcopy(inputs.candidate), copy.deepcopy(inputs.page_states)
        database, backend, evidence = inputs.database, inputs.backend, inputs.evidence
        missing_page = any(not any(row['name'] == page['name'] for row in trial_pages)
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
                page['analysis'] = self.page_analysis(state)
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
                                                   'analysis': self.page_analysis(state)}
            trial = self.design('ui', rid, trial, payload, backend)
            if record:
                old = next((index for index, row in enumerate(state['references']) if row['reference'] == reference), None)
                if old is None:
                    state['references'].append({key: record[key] for key in ('reference', 'sha256')}
                        | ({'image_id': page['image_id']} if page.get('image_id') else {}))
                else:
                    state['references'][old] = {key: record[key] for key in ('reference', 'sha256')}
                    if page.get('image_id'):
                        state['references'][old]['image_id'] = page['image_id']
        return FrontendTaskOutput(trial, trial_pages, requirement)


class DataDesignTask:
    def __init__(self, design, types):
        self.design, self.types = design, types

    def run(self, inputs: FrontendTaskInput) -> FrontendTaskOutput:
        rid, requirement = inputs.requirement_id, copy.deepcopy(inputs.requirement)
        trial, trial_pages = copy.deepcopy(inputs.candidate), copy.deepcopy(inputs.page_states)
        database, backend = inputs.database, inputs.backend
        data_access = database_context(database, rid)
        names = {page['name'] for page in requirement['pages']}
        pages, loaded = region(trial, names)
        apis = related_apis(backend, requirement, database)
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
        payload['existing_types'] = self.types(trial, apis, payload['existing_data'])
        trial = self.design('data', rid, trial, payload, backend)
        return FrontendTaskOutput(trial, trial_pages, requirement, payload)


class InteractionDesignTask:
    def __init__(self, design, types):
        self.design, self.types = design, types

    def run(self, inputs: FrontendTaskInput, context: dict[str, Any]) -> FrontendTaskOutput:
        rid, requirement = inputs.requirement_id, copy.deepcopy(inputs.requirement)
        trial, trial_pages = copy.deepcopy(inputs.candidate), copy.deepcopy(inputs.page_states)
        backend = inputs.backend
        payload = copy.deepcopy(context)
        names = {page['name'] for page in requirement['pages']}
        apis = payload['api_contracts']
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
        payload['existing_types'] = self.types(trial, apis, payload['existing_data'], payload['reuse_handlers'])
        payload['type_name_index'] = [row['name'] for row in trial['types']]
        trial = self.design('interaction', rid, trial, payload, backend)
        return FrontendTaskOutput(trial, trial_pages, requirement, payload)
