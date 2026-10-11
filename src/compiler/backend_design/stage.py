"""Postorder API/FUNC/DB design with layer-local context and parent integration."""
from __future__ import annotations

from ..ir import table_constraints

import copy
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog
from model.contracts import StructuredModel
from model.client import describe_model_error
from compiler.preprocessing.traversal import children_by_id, postorder
from .contracts import (
    LAYER_SCHEMAS, LAYER_PROMPTS,
    PARENT_DESIGN_SCHEMA, PARENT_DESIGN_PROMPT,
)
from .validation import shape_errors, reachable
from .context import module_contract, required_types, reuse_context, requirement_contracts


@dataclass
class BackendDesignResult:
    design_ir: dict[str, Any]
    node_states: dict[str, str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _budget(name: str, default: int, maximum: int) -> int:
    try:
        return max(0, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _closure(key: str, dependencies: dict[str, Any]) -> set[str]:
    pending, seen = list(dependencies.get(key, [])), set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(dependencies.get(current, []))
    return seen


def _scenario_ids(requirement: dict[str, Any], rid: str) -> set[str]:
    rows = requirement.get('scenarios', [])
    return {str(row.get('id') or row.get('scenario_id') or f'{rid}:scenario:{index + 1}')
            for index, row in enumerate(rows) if isinstance(row, dict)}


def _tree_events(requirement_ir: dict[str, Any]) -> list[tuple[str, str]]:
    children = children_by_id(requirement_ir)
    return [('parent' if children[key] else 'leaf', key) for key in postorder(requirement_ir)]


def project_design_context(schema: dict[str, Any], requirement_id: str, dependencies: dict[str, Any]) -> dict[str, Any]:
    """Select associated authoritative tables, then include direct FK neighbors."""
    wanted = {requirement_id} | _closure(requirement_id, dependencies)
    links = schema.get('traceability', {}).get('requirements', {})
    keys = set()
    for rid in wanted:
        keys.update(links.get(rid, {}).get('tables', []))
    selected = {row['name'] for row in schema.get('tables', [])
                if set(row.get('requirement_ids', [])) & wanted or row['name'] in keys}
    for row in schema.get('tables', []):
        for constraint in table_constraints(row):
            target = constraint.get('referenced_table')
            if row['name'] in selected and target:
                keys.add(target)
            if target in selected:
                keys.add(row['name'])
    selected |= keys
    return {'tables': [copy.deepcopy(row) for row in schema.get('tables', []) if row['name'] in selected]}


def _related_data(schema: dict[str, Any], rid: str) -> dict[str, list[str]]:
    links = schema.get('traceability', {}).get('requirements', {}).get(rid, {})
    result: dict[str, set[str]] = {name: set() for name in links.get('tables', [])}
    for reference in links.get('columns', []):
        table, _, column = reference.partition('.')
        if column:
            result.setdefault(table, set()).add(column)
    for table in schema.get('tables', []):
        if rid in table.get('requirement_ids', []):
            result.setdefault(table['name'], set()).update(
                row['name'] for row in table['columns'] if rid in row.get('requirement_ids', []))
    return {name: sorted(columns) for name, columns in result.items()}


def _candidates(ir: dict[str, Any], rid: str, dependencies: dict[str, Any], tables: set[str], spec: str, kinds: set[str]) -> list[dict[str, Any]]:
    modules = {row['id']: row for row in ir['modules']}
    tokens = set(re.findall(r'\w+', spec.lower()))
    ancestors = _closure(rid, dependencies)
    scored = []
    for module in modules.values():
        if module["kind"] not in kinds:
            continue
        graph = reachable([module['id']], modules)
        data = {access['table'] for key in graph for access in modules[key].get('data_access', [])}
        overlap = len(tokens & set(re.findall(r'\w+', json.dumps(module, ensure_ascii=False).lower())))
        score = 20 * len(data & tables) + 30 * bool(set(module['requirement_ids']) & ancestors) + overlap
        if score:
            scored.append((score, module['id']))
    selected = [key for _, key in sorted(scored, key=lambda row: (-row[0], row[1]))[:8]]
    return [copy.deepcopy(modules[key]) for key in sorted(selected)]


class BackendDesignPass:
    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self.model = model
        self.root = Path(artifact_root) / 'design' / 'backend'
        if hasattr(model, 'set_usage_path'):
            model.set_usage_path(Path(artifact_root) / 'model_usage.jsonl')
        self.log = SynchronousLog('BackendDesignPass', workspace_root=Path(artifact_root).resolve().parent)
        self.started_at = time.perf_counter()
        self.retries = _budget('ARC_STRUCTURED_OUTPUT_RETRY_COUNT', 2, 10)
        self.sequence = 0

    def _decision(self, phase: str, unit: str, prompt: str, schema: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        feedback: list[str] = []
        attempts = 1 if phase == "parent_composition" else self.retries + 1
        for attempt in range(attempts):
            request = copy.deepcopy(payload)
            if feedback:
                request['validation_feedback'] = feedback
            self.sequence += 1
            call_sequence = self.sequence
            trace = {'phase': phase, 'unit': unit, 'attempt': attempt + 1, 'input': request,
                     'instructions': prompt, 'output_schema': schema}
            started = time.perf_counter()
            self.log.info(
                f'MODEL_REQUEST phase={phase} call={self.sequence} '
                f'attempt={attempt + 1}/{attempts} '
                f'input_chars={len(json.dumps(request, ensure_ascii=False))}', node_id=unit)
            try:
                value = self.model.generate_json(schema_name=f'arc_backend_{phase}', instructions=prompt,
                                                 input_payload=request,
                                                 output_schema={**schema, 'x-arc-output-mode': 'json_object'})
                feedback = shape_errors(value, schema)
                trace.update(output=value, errors=feedback)
            except Exception as exc:
                feedback = [describe_model_error(exc)]
                trace['errors'] = feedback
                value = None
            duration_ms = int((time.perf_counter() - started) * 1000)
            trace['duration_ms'] = duration_ms
            trace_path = self.root / 'passes' / f'{call_sequence:05d}_{phase}.json'
            write_json_atomic(trace_path, trace)
            if not feedback:
                self.log.info(f'MODEL_ACCEPTED phase={phase} duration_ms={duration_ms} trace={trace_path}', node_id=unit)
                return value
            self.log.info(f'MODEL_REJECTED phase={phase} duration_ms={duration_ms} '
                          f'errors={len(feedback)} trace={trace_path}', node_id=unit)
            for error in feedback:
                self.log.info(f'VALIDATION_FEEDBACK phase={phase}: {error}', node_id=unit)
        self.log.info(f'MODEL_FAILED phase={phase} attempts={attempts}', node_id=unit)
        raise ValueError(f'{phase} {unit}: ' + '; '.join(feedback))

    def compile(self, requirement_ir: dict[str, Any], dependency_graph: dict[str, Any], database_schema: dict[str, Any]) -> BackendDesignResult:
        ir = {'ir_kind': 'backend_design',
              'schema_ref': '.arc/design/database/schema.json', 'schema_hash': _hash(database_schema),
              'types': [], 'modules': []}
        states, errors = {}, []
        self.pending_capabilities = []
        self.sequence = 0
        self.node_order = []
        self.inherited_context = []
        self.started_at = time.perf_counter()
        nodes = requirement_ir.get('nodes', {})
        self.requirement_nodes = nodes
        atomic = list(dict.fromkeys(requirement_ir.get('atomic_units', [])))
        dependencies = dependency_graph.get('atomic_dependencies', {})
        self.log.info(f'BACKEND_DESIGN_STARTED requirements={len(atomic)} '
                      f'tables={len(database_schema.get("tables", []))} traversal=depth_first_postorder '
                      f'output={self.root}')
        try:
            events = _tree_events(requirement_ir)
            self.node_order = [key for _, key in events]
            order = [key for phase, key in events if phase == 'leaf']
            self.children = children_by_id(requirement_ir)
        except ValueError as exc:
            errors.append(str(exc))
            order = []
            events = []
        scenarios = set().union(*(_scenario_ids(nodes.get(rid, {}), rid) for rid in nodes)) if nodes else set()
        if database_schema.get('status') != 'RESOLVED' or not isinstance(database_schema.get('tables'), list):
            errors.append('Backend design requires a RESOLVED database-neutral schema with tables')
        if errors:
            return self._finish(ir, states, errors)
        index = 0
        for event, rid in events:
            self.inherited_context = []
            ancestor = nodes[rid].get('parent_id')
            seen_ancestors = set()
            while ancestor in nodes and ancestor not in seen_ancestors:
                seen_ancestors.add(ancestor)
                self.inherited_context.insert(0, {'requirement_id': ancestor,
                    'requirement': copy.deepcopy(nodes[ancestor]),
                    'scenario_ids': sorted(_scenario_ids(nodes[ancestor], ancestor))})
                ancestor = nodes[ancestor].get('parent_id')
            if event == 'parent':
                try:
                    if any(states.get(child) != 'DESIGN_VALIDATED' for child in self.children[rid]):
                        raise ValueError('Parent cannot integrate: child design failed')
                    ir = self._compose_parent(ir, rid, nodes, database_schema, scenarios)
                    states[rid] = 'DESIGN_VALIDATED'
                    write_json_atomic(self.root / 'backend_ir.json', ir)
                    self.log.info('PARENT_DESIGN_COMMITTED', node_id=rid)
                except ValueError as exc:
                    states[rid] = 'FAILED'
                    errors.append(f'{rid}: {exc}')
                    self.log.info(f'PARENT_DESIGN_FAILED {exc}', node_id=rid)
                continue
            index += 1
            self.log.info(f'REQUIREMENT_STARTED progress={index}/{len(order)} '
                          f'name={nodes.get(rid, {}).get("name", "")}', node_id=rid)
            if rid not in nodes:
                states[rid] = 'FAILED'
                errors.append(f'{rid}: missing requirement node')
                self.log.info('REQUIREMENT_FAILED missing requirement node', node_id=rid)
                continue
            try:
                ir = self._design_layers(ir, rid, database_schema, dependencies, scenarios)
                states[rid] = 'DESIGN_VALIDATED'
                write_json_atomic(self.root / 'backend_ir.json', ir)
                self.log.info(f'REQUIREMENT_VALIDATED progress={index}/{len(order)} '
                              f'total_modules={len(ir["modules"])}', node_id=rid)
            except ValueError as exc:
                states[rid] = 'FAILED'
                errors.append(f'{rid}: {exc}')
                self.log.info(f'REQUIREMENT_FAILED {exc}', node_id=rid)
        return self._finish(ir, states, errors)

    def _design_layers(self, base: dict[str, Any], rid: str, schema: dict[str, Any],
                       dependencies: dict[str, Any], scenarios: set[str]) -> dict[str, Any]:
        trial = copy.deepcopy(base)
        api_ids: list[str] = []
        for kind in ('API', 'FUNC', 'DB'):
            if kind != 'API' and not api_ids:
                break
            trial, selected = self._design_layer(trial, rid, kind, api_ids, schema, dependencies, scenarios)
            if kind == 'API':
                api_ids = selected
        modules = {row['id']: row for row in trial['modules']}
        for key in reachable(api_ids, modules):
            modules[key]['requirement_ids'] = sorted(set(modules[key]['requirement_ids']) | {rid})
        return trial

    def _design_layer(self, base: dict[str, Any], rid: str, kind: str, api_ids: list[str],
                      schema: dict[str, Any], dependencies: dict[str, Any],
                      scenarios: set[str]) -> tuple[dict[str, Any], list[str]]:
        kinds = {'API': {'API', 'FUNC'}, 'FUNC': {'FUNC', 'DB'}, 'DB': {'DB'}}[kind]
        requirement = {key: copy.deepcopy(value) for key, value in self.requirement_nodes[rid].items()
                       if key in {'id', 'name', 'description', 'scenarios'}}
        for index, row in enumerate(requirement.get('scenarios', [])):
            if isinstance(row, dict):
                row.setdefault('id', row.get('scenario_id') or f'{rid}:scenario:{index + 1}')
        modules = {row['id']: row for row in base['modules']}
        current = reachable(api_ids, modules)
        pending = [{'caller_id': key, **copy.deepcopy(call)} for key in sorted(current)
                   for call in modules[key].get('calls', []) if call['module_id'] not in modules]
        if kind != 'API' and not pending:
            return base, api_ids
        upstream_ids = {row['caller_id'] for row in pending}
        upstream = [copy.deepcopy(modules[key]) for key in sorted(upstream_ids)]
        related = _related_data(schema, rid) if kind == 'DB' else {}
        candidates = _candidates(base, rid, dependencies, set(related), str(requirement), kinds)
        # Accepted upper-layer modules occur once in upstream_modules, never again in reuse_context.
        candidates = [row for row in candidates if row['id'] not in upstream_ids]
        tables = project_design_context(schema, rid, dependencies)['tables'] if kind == 'DB' else []
        requested_types: set[str] = set()
        for expansion in range(4):
            reuse = reuse_context(base, candidates, requirement, requested_types, kinds=kinds, upstream=upstream)
            payload = {'requirement_id': rid, 'requirement': requirement,
                       'ancestor_rules': [{'requirement_id': row['requirement_id'],
                                           'description': row['requirement'].get('description', ''),
                                           'scenarios': [dict(scenario, id=scenario.get('id') or scenario.get('scenario_id')
                                                              or f'{row["requirement_id"]}:scenario:{index + 1}')
                                                         for index, scenario in enumerate(row['requirement'].get('scenarios', []))
                                                         if isinstance(scenario, dict)]}
                                          for row in self.inherited_context],
                       'upstream_modules': upstream,
                       'pending_module_ids': sorted({row['module_id'] for row in pending}),
                       'reuse_context': reuse, 'context_requests_remaining': 3 - expansion}
            if kind == 'DB':
                payload['schema_slice'] = {'tables': tables}
                loaded_tables = {row['name'] for row in tables}
                payload['schema_index'] = [{'name': row['name'], 'description': row.get('description', '')}
                                           for row in schema['tables'] if row['name'] not in loaded_tables]

            decision = self._decision('design_' + kind.lower(), rid, LAYER_PROMPTS[kind],
                                      LAYER_SCHEMAS[kind], payload)
            # Context expansion is orchestration, not a semantic validation pass.
            has_design = any(decision.get(key) for key in
                             ('new_modules', 'new_types', 'reuse_module_ids', 'resolved_calls', 'api_ids'))
            loaded_modules = {row['id'] for row in candidates}
            fresh_modules = set(decision['requested_module_ids']) & modules.keys() - loaded_modules
            fresh_types = set(decision['requested_type_ids']) & {row['id'] for row in base['types']} - {row['id'] for row in reuse['types']}
            fresh_tables = set(decision.get('requested_tables', [])) & {row['name'] for row in schema['tables']} - {row['name'] for row in tables}
            if has_design or not (fresh_modules or fresh_types or fresh_tables) or expansion == 3:
                return self._materialize_layer(base, rid, decision, api_ids)
            requested_types.update(fresh_types)
            requested_modules = fresh_modules
            candidates.extend(copy.deepcopy(modules[key]) for key in sorted(requested_modules))
            requested_tables = fresh_tables
            tables.extend(copy.deepcopy(row) for row in schema['tables'] if row['name'] in requested_tables)
        raise ValueError('Layer context expansion budget exceeded')

    @staticmethod
    def _materialize_layer(base: dict[str, Any], rid: str, value: dict[str, Any],
                           api_ids: list[str]) -> tuple[dict[str, Any], list[str]]:
        """Merge shaped JSON and qualify local IDs without enforcing design policy."""
        trial = copy.deepcopy(base)
        modules = {row['id']: row for row in trial['modules']}
        types = {row['id']: row for row in trial['types']}
        new_ids = {row['id'] for row in value['new_modules']}

        def qualify(key: str) -> str:
            return key if '::' in key or key in modules and key not in new_ids else f'{rid}::{key}'

        mapping = {key: qualify(key) for key in new_ids}
        for row in value['new_types']:
            types.setdefault(row['id'], copy.deepcopy(row))
        for source in value['new_modules']:
            module = copy.deepcopy(source)
            module['id'] = mapping[source['id']]
            previous = modules.get(module['id'], {})
            module['requirement_ids'] = sorted(set(previous.get('requirement_ids', [])) | {rid})
            for call in module.get('calls', []):
                call['module_id'] = mapping.get(call['module_id'], qualify(call['module_id']))
            modules[module['id']] = module
        resolutions = {qualify(source): mapping.get(target, qualify(target))
                       for source, target in value['resolved_calls'].items()}
        for module in modules.values():
            for call in module.get('calls', []):
                call['module_id'] = resolutions.get(call['module_id'], call['module_id'])
        entries = [mapping.get(key, qualify(key)) for key in value.get('api_ids', api_ids)]
        trial['types'], trial['modules'] = list(types.values()), list(modules.values())
        return trial, entries

    def _compose_parent(self, base: dict[str, Any], rid: str, nodes: dict[str, Any], schema: dict[str, Any], scenarios: set[str]) -> dict[str, Any]:
        self.log.info('PARENT_INTEGRATION_STARTED', node_id=rid)
        descendants, pending = set(), list(self.children[rid])
        while pending:
            key = pending.pop()
            if key not in descendants:
                descendants.add(key)
                pending.extend(self.children[key])
        modules = {row['id']: row for row in base['modules']}
        available = {key for key, module in modules.items() if module['kind'] == 'API'
                     and descendants & set(module['requirement_ids'])}
        payload = {'requirement_id': rid, 'requirement': nodes[rid],
                   'ancestor_context': self.inherited_context,
                   'child_requirements': [nodes[key] for key in self.children[rid]],
                   'available_api_ids': sorted(available),
                   'api_contracts': [module_contract(modules[key]) for key in sorted(available)],
                   'pending_capabilities': copy.deepcopy(self.pending_capabilities)}
        payload['types'] = required_types(payload, base['types'])

        decision = self._decision('parent_composition', rid, PARENT_DESIGN_PROMPT,
                                  PARENT_DESIGN_SCHEMA, payload)
        trial = copy.deepcopy(base)
        targets = {row['id']: row for row in trial['modules']}
        for key in reachable(decision['api_ids'], targets):
            targets[key]['requirement_ids'] = sorted(set(targets[key]['requirement_ids']) | {rid})
        for gap in decision['pending_capabilities']:
            gap['requirement_ids'] = sorted(set(gap['requirement_ids']) | {rid})
            self.pending_capabilities.append(gap)
        return trial

    def _finish(self, ir: dict[str, Any], states: dict[str, str], errors: list[str]) -> BackendDesignResult:
        errors = list(dict.fromkeys(errors))
        pending: dict[str, Any] = {}
        for source in self.pending_capabilities:
            gap = copy.deepcopy(source)
            prior = pending.get(gap['id'], {})
            gap['requirement_ids'] = sorted(set(prior.get('requirement_ids', [])) | set(gap['requirement_ids']))
            gap['function_ids'] = sorted(set(prior.get('function_ids', [])) | set(gap['function_ids']))
            pending[gap['id']] = gap
        warnings = [f"{gap['id']}: pending backend capability: {gap['capability']} ({gap['reason']})"
                    for gap in pending.values()]
        write_json_atomic(self.root / 'backend_ir.json', ir)
        write_json_atomic(self.root / 'requirement_contracts.json', requirement_contracts(ir, self.node_order))
        write_json_atomic(self.root / 'report.json', {'errors': errors, 'warnings': warnings,
                                                    'pending_capabilities': list(pending.values()),
                                                    'status': 'FAILED' if errors else 'GENERATED_WITH_WARNINGS' if warnings else 'GENERATED',
                                                    'model_calls': self.sequence,
                                                    'traversal': 'depth_first_postorder', 'node_order': self.node_order})
        self.log.info(f'BACKEND_DESIGN_FINISHED success={not errors} '
                      f'requirements={len(states)} modules={len(ir["modules"])} '
                      f'model_calls={self.sequence} errors={len(errors)} '
                      f'duration_seconds={time.perf_counter() - self.started_at:.1f} '
                      f'ir={self.root / "backend_ir.json"} report={self.root / "report.json"}')
        for error in errors:
            self.log.info(f'BACKEND_DESIGN_ERROR {error}')
        return BackendDesignResult(ir, states, errors, warnings)
