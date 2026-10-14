"""Atomic, leaf-only API -> FUNC -> DB design with validated repair attempts."""
from __future__ import annotations
import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from compiler.preprocessing.traversal import postorder, children_by_id
from model.client import describe_model_error
from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog
from .contracts import PROMPTS, SCHEMAS
from .context import database_context, retrieve
from .validation import shape_errors, validate_ir, parse_contract


@dataclass
class BackendDesignResult:
    ir: dict[str, Any]
    node_states: dict[str, str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


class BackendDesignPass:
    def __init__(self, model, artifact_root: Path):
        self.model = model
        self.artifact_root = Path(artifact_root)
        self.root = self.artifact_root / 'design/backend'
        self.log = SynchronousLog('BackendDesignPass', workspace_root=self.artifact_root.parent)
        try:
            self.attempts = max(1, min(20, int(os.getenv('MODEL_ATTEMPT', '5'))))
        except ValueError:
            self.attempts = 5
        self.sequence = 0
        if hasattr(model, 'set_usage_path'):
            model.set_usage_path(self.artifact_root / 'model_usage.jsonl')

    def compile(self, requirements, database):
        if database.get('ir_kind') != 'er_model' or database.get('status') != 'RESOLVED':
            raise ValueError('Backend design requires a RESOLVED er_model.json')
        ir = {'ir_kind': 'backend_design', 'types': [], 'modules': []}
        children = children_by_id(requirements)
        order = [rid for rid in postorder(requirements) if not children[rid]]
        states, warnings = {}, []
        for rid in order:
            self.log.info('Leaf design: RUNNING', node_id=rid)
            trial = copy.deepcopy(ir)
            node = requirements['nodes'][rid]
            requirement = self._requirement(node, requirements['nodes'])
            data = database_context(database, rid)
            selected = []
            try:
                for kind in ('API', 'FUNC', 'DB'):
                    if kind != 'API' and not selected:
                        break
                    selected_upstream = [row for row in trial['modules'] if row['id'] in selected]
                    candidates = retrieve(trial, kind, requirement, data, database.get('access', {}))
                    # Existing lower contracts are immutable reference targets, not new designs.
                    reference_ids = {row['id'] for row in candidates + selected_upstream}
                    by_id = {row['id']: row for row in trial['modules']}
                    pending = list(reference_ids)
                    while pending:
                        for target in by_id[pending.pop()]['callees']:
                            if target not in reference_ids:
                                reference_ids.add(target)
                                pending.append(target)
                    references = [row for row in trial['modules'] if row['id'] in reference_ids and row['kind'] in
                                  ({'FUNC', 'DB'} if kind == 'FUNC' else {'DB'} if kind == 'DB' else set())]
                    payload = {'current_task': {'requirement': requirement, 'data': data},
                               'upstream_modules': selected_upstream, 'reuse_candidates': candidates,
                               'existing_types': trial['types'],
                               'reference_catalog': references,
                               'name_index': [row['name'] for row in trial['modules']],
                               'route_index': [{'id': row['id'], 'method': row['method'], 'path': row['path']}
                                               for row in trial['modules'] if row['kind'] == 'API']}
                    trial, selected = self._decision(kind, rid, trial, selected, payload, database)
                errors = validate_ir(trial, database)
                if errors:
                    raise ValueError('; '.join(errors))
                ir = trial
                states[rid] = 'DESIGNED'
                self.log.info('Leaf design: COMPLETED', node_id=rid)
                write_json_atomic(self.root / 'backend_ir.json', ir)
            except ValueError as exc:
                states[rid] = 'SKIPPED'
                warnings.append(f'{rid}: rolled back leaf design after failed attempts: {exc}')
                self.log.warning(warnings[-1], node_id=rid)
        result = BackendDesignResult(ir, states, warnings=warnings)
        write_json_atomic(self.root / 'backend_ir.json', ir)
        write_json_atomic(self.root / 'report.json', {'status': 'GENERATED_WITH_WARNINGS' if warnings else 'GENERATED',
            'node_states': states, 'warnings': warnings, 'errors': [], 'leaf_order': order,
            'model_attempt': self.attempts, 'model_calls': self.sequence})
        return result

    def _requirement(self, node, nodes):
        result = {key: copy.deepcopy(node[key]) for key in ('id', 'name', 'description', 'scenarios', 'pages') if key in node}
        # Visual stage stores analysis separately; attach only this leaf's reference evidence.
        cache_path = self.artifact_root / 'visual_cache.json'
        cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
        for page in result.get('pages', []):
            reference = page.get('reference')
            normalized = str(reference or '').replace('\\', '/')
            record = cache.get(normalized, {})
            page['analysis'] = page.get('analysis', record.get('analysis') if isinstance(record, dict) else None)
        ancestors, seen = [], set()
        parent = node.get('parent_id')
        while parent in nodes and parent not in seen:
            seen.add(parent)
            ancestors.append({key: nodes[parent].get(key, '') for key in ('id', 'name', 'description')})
            parent = nodes[parent].get('parent_id')
        result['ancestor_constraints'] = list(reversed(ancestors))
        return result

    def _decision(self, kind, rid, base, upstream, payload, database):
        feedback, previous = [], None
        for attempt in range(self.attempts):
            self.log.info(f'{kind} design attempt {attempt + 1}/{self.attempts}', node_id=rid)
            request = copy.deepcopy(payload)
            if feedback:
                request['validation_feedback'], request['previous_output'] = feedback, previous
            self.sequence += 1
            trace = {'requirement_id': rid, 'phase': kind, 'attempt': attempt + 1,
                     'instructions': PROMPTS[kind], 'input': request}
            try:
                output = self.model.generate_json(schema_name='backend_' + kind.lower(), instructions=PROMPTS[kind],
                                                 input_payload=request, output_schema=SCHEMAS[kind])
                previous = output
                trace['output'] = output
                feedback = shape_errors(output, SCHEMAS[kind])
                if not feedback:
                    candidate, selected = self._materialize(base, kind, rid, upstream, output, payload)
                    feedback = validate_ir(candidate, database, complete=kind == 'DB')
                    if not feedback:
                        trace['errors'] = []
                        write_json_atomic(self.root / 'passes' / f'{self.sequence:05d}_{kind.lower()}.json', trace)
                        return candidate, selected
            except Exception as exc:
                feedback = [describe_model_error(exc)]
            trace['errors'] = feedback
            self.log.warning(f'{kind} validation rejected: ' + '; '.join(feedback), node_id=rid)
            write_json_atomic(self.root / 'passes' / f'{self.sequence:05d}_{kind.lower()}.json', trace)
        raise ValueError(f'{kind}: ' + '; '.join(feedback))

    @staticmethod
    def _materialize(base, kind, rid, upstream, output, payload):
        trial = copy.deepcopy(base)
        modules = {row['id']: row for row in trial['modules']}
        types = {row['name']: row['schema'] for row in trial['types']}
        for row in output['new_types']:
            if row['name'] in types:
                raise ValueError(f'Duplicate/invalid type name: {row["name"]}')
            types[row['name']] = row['schema']
            trial['types'].append(copy.deepcopy(row))
        for row in output['new_types']:
            parse_contract(row['schema'], row['name'], types, interface=False)
        new_by_name, selected = {}, []
        for source in output['new_modules']:
            if source['name'] in new_by_name:
                raise ValueError('Duplicate new module name ' + source['name'])
            ordinal = 1 + sum(row['kind'] == kind for row in modules.values())
            key = f'{kind}_{ordinal:06d}'
            row = {**copy.deepcopy(source), 'id': key, 'kind': kind, 'reqs': [rid], 'callers': [], 'callees': []}
            modules[key] = row
            new_by_name[source['name']] = key
            selected.append(key)
        referenced_types = set()
        def collect_types(value):
            if isinstance(value, dict):
                reference = value.get('$ref', '')
                if isinstance(reference, str) and reference.startswith('#/types/'):
                    name = reference[8:]
                    if name in types and name not in referenced_types:
                        referenced_types.add(name)
                        collect_types(json.loads(types[name]))
                for child in value.values():
                    collect_types(child)
            elif isinstance(value, list):
                for child in value:
                    collect_types(child)
        for row in output['new_modules']:
            for direction in (('payload', 'response') if kind == 'API' else ('input', 'output')):
                collect_types(json.loads(row[direction]))
        unused_types = {row['name'] for row in output['new_types']} - referenced_types
        if unused_types:
            raise ValueError(f'Unused new type definitions: {sorted(unused_types)}')
        loaded = {row['id'] for row in [*payload['reuse_candidates'], *payload['upstream_modules'], *payload['reference_catalog']]}
        def resolve(key):
            if key.startswith('@'):
                if key[1:] not in new_by_name:
                    raise ValueError(f'Unknown local reference {key}')
                return new_by_name[key[1:]]
            if key not in loaded or key not in modules:
                raise ValueError(f'Unknown or unloaded module ID {key}')
            return key
        reused = set()
        for reuse in output['reuse']:
            key = reuse['id']
            if key not in {row['id'] for row in payload['reuse_candidates']} or key in reused:
                raise ValueError(f'Invalid or duplicate reuse ID {key}')
            reused.add(key)
            modules[key]['reqs'] = sorted(set(modules[key]['reqs']) | {rid})
            selected.append(key)
        def edge(source, target):
            if target not in modules[source]['callees']:
                if source in {row['id'] for row in base['modules']} and source not in upstream:
                    raise ValueError(f'Cannot change reused module callees: {source}')
                # Existing upstream API/FUNC can only be wired if it was newly designed for this leaf.
                original = next((row for row in base['modules'] if row['id'] == source), None)
                if original and original['reqs'] != [rid] and target not in original['callees']:
                    raise ValueError(f'Cannot extend immutable upstream module {source}')
                modules[source]['callees'].append(target)
            if source not in modules[target]['callers']:
                modules[target]['callers'].append(source)
        if kind != 'API':
            for source in output['new_modules']:
                key = new_by_name[source['name']]
                for caller in source['callers']:
                    origin = resolve(caller)
                    if origin not in upstream and origin not in new_by_name.values():
                        raise ValueError(f'Caller must be supplied upstream or local: {caller}')
                    edge(origin, key)
                for callee in source['callees']:
                    edge(key, resolve(callee))
            for reuse in output['reuse']:
                for caller in reuse['callers']:
                    origin = resolve(caller)
                    if origin not in upstream and origin not in new_by_name.values():
                        raise ValueError(f'Invalid reuse caller {caller}')
                    edge(origin, reuse['id'])
            for key in upstream:
                if not modules[key]['callees']:
                    raise ValueError(f'Unfulfilled upstream module {key}: declare at least one collaborator')
        # Reject unused helpers before committing a layer.
        roots = selected if kind == 'API' else upstream
        visited, pending = set(), list(roots)
        while pending:
            key = pending.pop()
            if key in visited:
                continue
            visited.add(key)
            pending.extend(modules[key]['callees'])
        if set(selected) - visited:
            raise ValueError(f'Unreachable new/reused modules: {sorted(set(selected) - visited)}')
        # Selecting an unchanged API/function brings its already established graph, not redesign work.
        for key in visited:
            modules[key]['reqs'] = sorted(set(modules[key]['reqs']) | {rid})
        trial['modules'] = list(modules.values())
        next_selected = set(selected)
        for key in visited:
            if modules[key]['kind'] == kind:
                next_selected.add(key)
        return trial, sorted(next_selected)
