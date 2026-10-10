"""Postorder leaf design and single-pass parent integration using existing APIs."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from arcbench_agent_runtime.jsonio import write_json_atomic
from core.logging import SynchronousLog
from ..model_client import StructuredModel, describe_model_error
from ..requirement_traversal import children_by_id, postorder
from .contracts import (
    ANALYSIS_SCHEMA, ARCHITECTURE_SCHEMA, MODULE_SCHEMA,
    ANALYSIS_PROMPT, ARCHITECTURE_PROMPT, REFINEMENT_PROMPT,
    PARENT_DESIGN_SCHEMA, PARENT_DESIGN_PROMPT,
)
from .validation import shape_errors, validate_data_access, validate_backend_ir, access_set, reachable
from .context import module_contract, refinement_context, required_types


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
        keys.update(links.get(rid, {}).get('entities', []))
    selected = {row['name'] for row in schema.get('tables', [])
                if set(row.get('requirement_ids', [])) & wanted or row['name'] in keys}
    for row in schema.get('tables', []):
        for constraint in row.get('constraints', []):
            target = constraint.get('referenced_table')
            if row['name'] in selected and target:
                keys.add(target)
            if target in selected:
                keys.add(row['name'])
    selected |= keys
    return {'tables': [copy.deepcopy(row) for row in schema.get('tables', []) if row['name'] in selected],
            'runtime_rules': [copy.deepcopy(row) for row in schema.get('runtime_rules', [])
                              if not row.get('requirement_ids') or set(row['requirement_ids']) & wanted]}


def _related_data(schema: dict[str, Any], rid: str) -> dict[str, list[str]]:
    links = schema.get('traceability', {}).get('requirements', {}).get(rid, {})
    result: dict[str, set[str]] = {name: set() for name in links.get('entities', [])}
    for reference in links.get('fields', []):
        table, _, column = reference.partition('.')
        if column:
            result.setdefault(table, set()).add(column)
    for table in schema.get('tables', []):
        if rid in table.get('requirement_ids', []):
            result.setdefault(table['name'], set()).update(
                row['name'] for row in table['columns'] if rid in row.get('requirement_ids', []))
    return {name: sorted(columns) for name, columns in result.items()}


def _candidates(ir: dict[str, Any], rid: str, dependencies: dict[str, Any], tables: set[str], spec: str) -> list[dict[str, Any]]:
    modules = {row['id']: row for row in ir['modules']}
    tokens = set(re.findall(r'\w+', spec.lower()))
    ancestors = _closure(rid, dependencies)
    scored = []
    for module in modules.values():
        graph = reachable([module['id']], modules)
        data = {access['table'] for key in graph for access in modules[key]['data_access']}
        overlap = len(tokens & set(re.findall(r'\w+', module['spec'].lower())))
        score = 20 * len(data & tables) + 30 * bool(set(module['requirement_ids']) & ancestors) + overlap
        if score:
            scored.append((score, module['id']))
    selected = [key for _, key in sorted(scored, key=lambda row: (-row[0], row[1]))[:24]]
    # Include full descendant interfaces for selected shared API/FUNC modules.
    selected = reachable(selected, modules)
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
        self.reopen_budget = _budget('ARC_DESIGN_REOPEN_COUNT', 2, 6)
        self.sequence = 0

    def _decision(self, phase: str, unit: str, prompt: str, schema: dict[str, Any], payload: dict[str, Any],
                  validator: Callable[[dict[str, Any]], list[str]]) -> dict[str, Any]:
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
                if not feedback:
                    feedback = validator(value)
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
              'types': [], 'requirements': [], 'modules': []}
        states, errors = {}, []
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
                analysis, context = self._analyze(ir, rid, nodes[rid], database_schema, dependencies)
                self.log.info(f'ANALYSIS_COMPLETED server_state={analysis["server_state"]} '
                              f'data_operations={len(analysis["data_operations"])} '
                              f'criteria={len(analysis["acceptance_criteria"])}', node_id=rid)
                feedback = []
                for reopen in range(self.reopen_budget + 1):
                    try:
                        trial = self._architecture(ir, rid, analysis, context, database_schema, dependencies, scenarios, feedback)
                        self.log.info(f'ARCHITECTURE_COMPLETED new_modules={len(trial["modules"]) - len(ir["modules"])} '
                                      f'total_modules={len(trial["modules"])}', node_id=rid)
                        self._refine(trial, ir, rid, analysis, context, database_schema, scenarios)
                        problems = validate_backend_ir(trial, database_schema, scenarios)
                        if problems:
                            raise ValueError('; '.join(problems))
                        ir = trial
                        states[rid] = 'DESIGN_VALIDATED'
                        write_json_atomic(self.root / 'backend_ir.json', ir)
                        self.log.info(f'REQUIREMENT_VALIDATED progress={index}/{len(order)} '
                                      f'total_modules={len(ir["modules"])}', node_id=rid)
                        break
                    except ValueError as exc:
                        feedback = [str(exc)]
                        self.log.info(f'ARCHITECTURE_REJECTED round={reopen + 1}/{self.reopen_budget + 1} '
                                      f'feedback={exc}', node_id=rid)
                        if reopen == self.reopen_budget:
                            raise
                        self.log.info('ARCHITECTURE_REOPEN retrying module design', node_id=rid)
            except ValueError as exc:
                states[rid] = 'FAILED'
                errors.append(f'{rid}: {exc}')
                self.log.info(f'REQUIREMENT_FAILED {exc}', node_id=rid)
        self.log.info(f'GLOBAL_VALIDATION_STARTED requirements={len(ir["requirements"])} modules={len(ir["modules"])}')
        errors += validate_backend_ir(ir, database_schema, scenarios)
        if {key for _, key in events} != {row['requirement_id'] for row in ir['requirements']}:
            errors.append('Final backend IR does not cover every requirement tree node')
        return self._finish(ir, states, errors)

    def _analyze(self, ir: dict[str, Any], rid: str, requirement: dict[str, Any], schema: dict[str, Any], dependencies: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        context = project_design_context(schema, rid, dependencies)
        related = _related_data(schema, rid)
        payload = {'requirement_id': rid, 'requirement': requirement,
                   'scenario_ids': sorted(_scenario_ids(requirement, rid)), 'related_data': related,
                   'ancestor_context': self.inherited_context,
                   'schema_index': [{'name': row['name'], 'description': row['description']} for row in schema['tables']],
                   'types': ir['types'], 'dependency_requirements': [row for row in ir['requirements'] if row['requirement_id'] in _closure(rid, dependencies)],
                   'existing_modules': _candidates(ir, rid, dependencies, set(related), str(requirement))}
        self.log.info(f'ANALYSIS_CONTEXT tables={len(context["tables"])} '
                      f'existing_modules={len(payload["existing_modules"])}', node_id=rid)
        for _ in range(3):
            def validate(value: dict[str, Any]) -> list[str]:
                problems = validate_data_access(value['data_operations'], schema)
                covered = {key for row in value['acceptance_criteria'] for key in row['scenario_ids']}
                own_scenarios = _scenario_ids(requirement, rid)
                inherited_scenarios = {key for context_row in self.inherited_context
                                       for key in context_row['scenario_ids']}
                if not own_scenarios <= covered or covered - own_scenarios - inherited_scenarios:
                    problems.append('analysis criteria must cover own scenarios and reference only own/inherited scenarios')
                if not value['acceptance_criteria'] or not value['spec'].strip():
                    problems.append('analysis needs nonempty spec and criteria')
                if not value['server_state'] and (value['data_operations'] or value['context']):
                    problems.append('client-local requirements cannot have server operations/context')
                capabilities = value['api_capabilities']
                operations = [row['operation'] for row in capabilities]
                if (len(operations) != len(set(operations)) or any(not key.strip() for key in operations)
                        or bool(capabilities) != value['server_state']):
                    problems.append('server requirements need distinct API capabilities; client-local requirements need none')
                capability_scenarios = {key for row in capabilities for key in row['scenario_ids']}
                if value['server_state'] and not own_scenarios <= capability_scenarios:
                    problems.append('API capabilities must cover every complete own scenario')
                for capability in capabilities:
                    if not capability['purpose'].strip() or set(capability['scenario_ids']) - own_scenarios - inherited_scenarios:
                        problems.append('API capability needs a purpose and supplied scenario IDs')
                    for direction in ('inputs', 'context', 'outputs'):
                        ids = capability[direction]
                        if len(ids) != len(set(ids)) or set(ids) - {row['semantic_id'] for row in value[direction]}:
                            problems.append(f'API capability {direction} must reference its analysis field catalog')
                names = {row['name'] for row in schema['tables']}
                if set(value['requested_tables']) - names:
                    problems.append('requested_tables contains unknown schema tables')
                visible = {row['name'] for row in context['tables']} | set(value['requested_tables'])
                if {row['table'] for row in value['data_operations']} - visible:
                    problems.append('request a schema table before declaring operations on it')
                return problems
            analysis = self._decision('analysis', rid, ANALYSIS_PROMPT, ANALYSIS_SCHEMA,
                                      {**payload, 'schema_slice': context}, validate)
            requested = set(analysis['requested_tables']) - {row['name'] for row in context['tables']}
            if not requested:
                return analysis, context
            context['tables'] += [copy.deepcopy(row) for row in schema['tables'] if row['name'] in requested]
            self.log.info(f'SCHEMA_EXPANDED added_tables={sorted(requested)} '
                          f'total_tables={len(context["tables"])}', node_id=rid)
        raise ValueError('schema expansion budget exhausted')

    def _architecture(self, base: dict[str, Any], rid: str, analysis: dict[str, Any], context: dict[str, Any], schema: dict[str, Any], dependencies: dict[str, Any], scenarios: set[str], feedback: list[str]) -> dict[str, Any]:
        candidates = _candidates(base, rid, dependencies, {row['table'] for row in analysis['data_operations']}, analysis['spec'])
        candidate_ids = {row['id'] for row in candidates}
        def materialize(value: dict[str, Any]) -> dict[str, Any]:
            trial = copy.deepcopy(base)
            types = {row['id']: row for row in trial['types']}
            for row in analysis['types']:
                if row['id'] in types and types[row['id']] != row:
                    raise ValueError(f'cannot redefine existing type {row["id"]}')
                types[row['id']] = row
            trial['types'] = list(types.values())
            names = [row['id'] for row in value['new_modules']]
            if len(names) != len(set(names)):
                raise ValueError('duplicate draft module id')
            mapping = {}
            for row in value['new_modules']:
                if not re.fullmatch(r'(API|FUNC|DB)\.[A-Za-z][A-Za-z0-9_]*', row['id']) or not row['id'].startswith(row['kind'] + '.'):
                    raise ValueError(f'invalid local module id {row["id"]}')
                mapping[row['id']] = f'{rid}::{row["id"]}'
            if set(value['reuse_module_ids']) - candidate_ids:
                raise ValueError('reuse references modules not supplied as candidates')
            for draft in value['new_modules']:
                module = copy.deepcopy(draft)
                module['id'] = mapping[module['id']]
                module['requirement_ids'] = [rid]
                module['acceptance_criteria'] = copy.deepcopy(analysis['acceptance_criteria'])
                if module['kind'] == 'API':
                    for direction in ('inputs', 'context', 'outputs'):
                        catalog = {row['semantic_id']: row for row in analysis[direction]}
                        for field in module[direction]:
                            reference = catalog.get(field['semantic_id'])
                            if reference is None or any(field[prop] != reference[prop] for prop in ('type', 'required', 'nullable')):
                                raise ValueError(f'API {direction} field is outside fixed analysis: {field["semantic_id"]}')
                for call in module['calls']:
                    call['module_id'] = mapping.get(call['module_id'], call['module_id'])
                    if call['module_id'] not in mapping.values() and call['module_id'] not in value['reuse_module_ids']:
                        raise ValueError('every existing direct callee must appear in reuse_module_ids')
                trial['modules'].append(module)
            api_ids = [mapping.get(key, key) for key in value['api_ids']]
            if any(key not in mapping.values() and key not in value['reuse_module_ids'] for key in api_ids):
                raise ValueError('existing API entry must appear in reuse_module_ids')
            related = _related_data(schema, rid)
            for access in analysis['data_operations']:
                related.setdefault(access['table'], [])
                related[access['table']] = sorted(set(related[access['table']]) | set(access['read'] + access['create'] + access['update']))
            trial['requirements'].append({'requirement_id': rid, 'spec': analysis['spec'], 'server_state': analysis['server_state'],
                                          'api_ids': api_ids, 'related_data': related,
                                          'acceptance_criteria': copy.deepcopy(analysis['acceptance_criteria'])})
            modules = {row['id']: row for row in trial['modules']}
            capabilities = {row['operation']: row for row in analysis['api_capabilities']}
            entries = [modules[key] for key in api_ids if key in modules and modules[key]['kind'] == 'API']
            if {row['entrypoint']['operation'] for row in entries} != set(capabilities):
                raise ValueError('API capability coverage differs from the complete scenario analysis')
            for entry in entries:
                capability = capabilities[entry['entrypoint']['operation']]
                for direction in ('inputs', 'context', 'outputs'):
                    if {field['semantic_id'] for field in entry[direction]} != set(capability[direction]):
                        raise ValueError(f'API {entry["id"]} {direction} differs from its analyzed capability')
            used = reachable(api_ids, modules)
            if set(value['reuse_module_ids']) - used:
                raise ValueError('unused reuse_module_ids')
            for key in used:
                modules[key]['requirement_ids'] = sorted(set(modules[key]['requirement_ids']) | {rid})
            actual = access_set([access for key in used for access in modules[key]['data_access']])
            expected = access_set(analysis['data_operations'])
            if actual != expected:
                raise ValueError(f'data operation coverage mismatch: missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}')
            return trial
        def validate(value: dict[str, Any]) -> list[str]:
            try:
                return validate_backend_ir(materialize(value), schema, scenarios)
            except ValueError as exc:
                return [str(exc)]
        decision = self._decision('architecture', rid, ARCHITECTURE_PROMPT, ARCHITECTURE_SCHEMA,
                                  {'requirement_id': rid, 'analysis': analysis, 'schema_slice': context,
                                   'existing_modules': candidates, 'architecture_feedback': feedback,
                                   'ancestor_context': self.inherited_context}, validate)
        return materialize(decision)

    def _refine(self, trial: dict[str, Any], base: dict[str, Any], rid: str, analysis: dict[str, Any], context: dict[str, Any], schema: dict[str, Any], scenarios: set[str]) -> None:
        modules = {row['id']: row for row in trial['modules']}
        old = {row['id'] for row in base['modules']}
        done = set(old)
        def refine(key: str) -> None:
            if key in done:
                return
            module = modules[key]
            for call in module['calls']:
                refine(call['module_id'])
            self.log.info(f'MODULE_REFINEMENT_STARTED kind={module["kind"]} '
                          f'callees={len(module["calls"])}', node_id=key)
            def validate(value: dict[str, Any]) -> list[str]:
                mutable = {'spec', 'acceptance_criteria'} | ({'data_access'} if module['kind'] == 'DB' else set())
                errors = [f'fixed module field changed: {field}' for field in module if field not in mutable and value[field] != module[field]]
                if access_set(value['data_access']) != access_set(module['data_access']):
                    errors.append('refinement changed fixed logical data operation scope')
                candidate = copy.deepcopy(trial)
                candidate['modules'] = [value if row['id'] == key else row for row in candidate['modules']]
                return errors + validate_backend_ir(candidate, schema, scenarios)
            payload = {'fixed_module': module,
                       'module_context': refinement_context(module, modules, context),
                       'allowed_scenario_ids': sorted({scenario for criterion in analysis['acceptance_criteria']
                                                      for scenario in criterion['scenario_ids']} |
                                                     {scenario for row in self.inherited_context
                                                      for scenario in row['scenario_ids']}),
                       'callees': [module_contract(modules[callee]) for callee in
                                   dict.fromkeys(call['module_id'] for call in module['calls'])]}
            payload['types'] = required_types(payload, trial['types'])
            value = self._decision('refinement', key, REFINEMENT_PROMPT, MODULE_SCHEMA, payload, validate)
            module.clear()
            module.update(value)
            done.add(key)
            self.log.info(f'MODULE_REFINEMENT_COMPLETED criteria={len(module["acceptance_criteria"])}', node_id=key)
        for module in list(modules.values()):
            refine(module['id'])

    def _compose_parent(self, base: dict[str, Any], rid: str, nodes: dict[str, Any], schema: dict[str, Any], scenarios: set[str]) -> dict[str, Any]:
        self.log.info('PARENT_INTEGRATION_STARTED', node_id=rid)
        descendants, pending = set(), list(self.children[rid])
        while pending:
            key = pending.pop()
            if key in descendants:
                continue
            descendants.add(key)
            pending.extend(self.children[key])
        requirements = [row for row in base['requirements'] if row['requirement_id'] in descendants]
        modules = {row['id']: row for row in base['modules']}
        available = {key for row in requirements for key in row['api_ids']}
        related_data: dict[str, list[str]] = {}
        for row in requirements:
            for table, columns in row['related_data'].items():
                related_data[table] = sorted(set(related_data.get(table, [])) | set(columns))

        def materialize(value: dict[str, Any]) -> dict[str, Any]:
            requirement = copy.deepcopy(value['requirement'])
            requirement['related_data'] = copy.deepcopy(related_data)
            requirement['pending_capabilities'] = copy.deepcopy(value['pending_capabilities'])
            for gap in requirement['pending_capabilities']:
                gap['requirement_ids'] = sorted(set(gap['requirement_ids']) | {rid})
            if requirement['requirement_id'] != rid:
                raise ValueError('Parent requirement id changed')
            if set(requirement['api_ids']) - available:
                raise ValueError('Parent may only associate existing descendant APIs; new interfaces are forbidden')
            covered = {key for row in requirement['acceptance_criteria'] for key in row['scenario_ids']}
            if not _scenario_ids(nodes[rid], rid) <= covered:
                raise ValueError('Parent scenarios are not fully covered')
            trial = copy.deepcopy(base)
            trial['requirements'].append(requirement)
            targets = {row['id']: row for row in trial['modules']}
            for key in reachable(requirement['api_ids'], targets):
                targets[key]['requirement_ids'] = sorted(set(targets[key]['requirement_ids']) | {rid})
            return trial

        def validate(value: dict[str, Any]) -> list[str]:
            try:
                return validate_backend_ir(materialize(value), schema, scenarios)
            except ValueError as exc:
                return [str(exc)]

        payload = {'requirement_id': rid, 'requirement': nodes[rid],
             'scenario_ids': sorted(_scenario_ids(nodes[rid], rid)), 'ancestor_context': self.inherited_context,
             'child_requirements': [{key: row[key] for key in
                                    ('requirement_id', 'api_ids', 'pending_capabilities') if key in row}
                                   for row in requirements], 'available_api_ids': sorted(available),
             'api_contracts': [module_contract(modules[key]) for key in sorted(available)],
             'related_data': related_data}
        payload['types'] = required_types(payload, base['types'])
        # Only an explicitly empty grouping node can bypass semantic integration.
        # A parent with prose may contain genuine capabilities missing from its children.
        if (str(nodes[rid].get('type', '')).upper() in {'CATEGORY', 'GROUP'}
                and not str(nodes[rid].get('description', '')).strip() and not nodes[rid].get('scenarios')):
            decision = {'requirement': {'requirement_id': rid,
                'spec': 'Aggregate completed descendant capabilities.', 'server_state': bool(available),
                'api_ids': sorted(available), 'related_data': related_data,
                'acceptance_criteria': [{'id': rid + '.composition', 'given': 'Completed descendant designs.',
                    'when': 'Their capabilities are composed.',
                    'then': 'Existing descendant API contracts and behavior are preserved.', 'scenario_ids': []}]},
                'pending_capabilities': []}
        else:
            decision = self._decision('parent_composition', rid, PARENT_DESIGN_PROMPT,
                                      PARENT_DESIGN_SCHEMA, payload, validate)
        return materialize(decision)

    def _finish(self, ir: dict[str, Any], states: dict[str, str], errors: list[str]) -> BackendDesignResult:
        errors = list(dict.fromkeys(errors))
        pending: dict[str, Any] = {}
        for row in ir['requirements']:
            for source in row.get('pending_capabilities', []):
                gap = copy.deepcopy(source)
                prior = pending.get(gap['id'], {})
                gap['requirement_ids'] = sorted(set(prior.get('requirement_ids', [])) | set(gap['requirement_ids']))
                gap['function_ids'] = sorted(set(prior.get('function_ids', [])) | set(gap['function_ids']))
                pending[gap['id']] = gap
        warnings = [f"{gap['id']}: pending backend capability: {gap['capability']} ({gap['reason']})"
                    for gap in pending.values()]
        write_json_atomic(self.root / 'backend_ir.json', ir)
        write_json_atomic(self.root / 'report.json', {'errors': errors, 'warnings': warnings,
                                                    'pending_capabilities': list(pending.values()),
                                                    'status': 'FAILED' if errors else 'GENERATED_WITH_WARNINGS' if warnings else 'GENERATED',
                                                    'model_calls': self.sequence,
                                                    'traversal': 'depth_first_postorder', 'node_order': self.node_order})
        self.log.info(f'BACKEND_DESIGN_FINISHED success={not errors} '
                      f'requirements={len(ir["requirements"])} modules={len(ir["modules"])} '
                      f'model_calls={self.sequence} errors={len(errors)} '
                      f'duration_seconds={time.perf_counter() - self.started_at:.1f} '
                      f'ir={self.root / "backend_ir.json"} report={self.root / "report.json"}')
        for error in errors:
            self.log.info(f'BACKEND_DESIGN_ERROR {error}')
        return BackendDesignResult(ir, states, errors, warnings)
