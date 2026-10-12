"""Postorder API/FUNC/DB design with layer-local context and parent integration."""
from __future__ import annotations

from ..ir import table_constraints

import copy
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog
from model.contracts import StructuredModel
from model.client import describe_model_error
from compiler.preprocessing.traversal import children_by_id, postorder
from .contracts import (
    LAYER_SCHEMAS, LAYER_PROMPTS,
    PARENT_DESIGN_SCHEMA, PARENT_DESIGN_PROMPT,
)
from .validation import (shape_errors, reachable, context_request_errors, ModuleConflictIndex,
                         graph_errors, contract_errors, layer_reference_errors)
from .validation import (semantic_type_summary, semantic_type_errors, semantic_type_registry,
                         semantic_revision_errors)
from .context import module_contract, required_types, reuse_context, requirement_contracts, requirement_reuse_candidates


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


class BackendDesignPass:
    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self.model = model
        self.root = Path(artifact_root) / 'design' / 'backend'
        if hasattr(model, 'set_usage_path'):
            model.set_usage_path(Path(artifact_root) / 'model_usage.jsonl')
        self.log = SynchronousLog('BackendDesignPass', workspace_root=Path(artifact_root).resolve().parent)
        self.started_at = time.perf_counter()
        # One initial response plus five correction opportunities.
        self.retries = _budget('ARC_STRUCTURED_OUTPUT_RETRY_COUNT', 5, 10)
        self.sequence = 0

    def _decision(self, phase: str, unit: str, prompt: str, schema: dict[str, Any], payload: dict[str, Any],
                  *, validate: Callable[[dict[str, Any]], list[str]] | None = None) -> dict[str, Any]:
        feedback: list[str] = []
        rejected_output: Any = None
        attempts = self.retries + 1
        for attempt in range(attempts):
            request = copy.deepcopy(payload)
            if feedback:
                request['validation_feedback'] = feedback
                if rejected_output is not None:
                    request['previous_output'] = rejected_output
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
                if not feedback and validate is not None:
                    feedback = validate(value)
                trace.update(output=value, errors=feedback)
                rejected_output = value
            except Exception as exc:
                feedback = [describe_model_error(exc)]
                trace['errors'] = feedback
                value = None
                rejected_output = None
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
        self.design_warnings: list[str] = []
        self.repair_context = None
        self.sequence = 0
        self.node_order = []
        self.inherited_context = []
        self.started_at = time.perf_counter()
        nodes = requirement_ir.get('nodes', {})
        self.requirement_nodes = nodes
        self.coverage_schema = database_schema
        artifact_root = self.root.parent.parent
        self.lowering_context = None
        project_path = artifact_root / 'project/project-manifest.json'
        database_path = artifact_root / 'lowering/database/manifest.json'
        if project_path.is_file() and database_path.is_file():
            self.lowering_context = (json.loads(project_path.read_text()), json.loads(database_path.read_text()),
                                     artifact_root.parent)
        atomic = list(dict.fromkeys(requirement_ir.get('atomic_units', [])))
        dependencies = dependency_graph.get('atomic_dependencies', {})
        self.log.info(f'BACKEND_DESIGN_STARTED requirements={len(atomic)} '
                      f'tables={len(database_schema.get("tables", []))} traversal=leaves_then_parent_coverage '
                      f'output={self.root}')
        try:
            events = _tree_events(requirement_ir)
            # Global coverage cannot be judged while sibling leaf designs are still absent.
            events = ([event for event in events if event[0] == 'leaf']
                      + [event for event in events if event[0] == 'parent'])
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
                    'requirement': {key: copy.deepcopy(value) for key, value in nodes[ancestor].items()
                                    if key not in {'environment', 'data'}},
                    'scenario_ids': sorted(_scenario_ids(nodes[ancestor], ancestor))})
                ancestor = nodes[ancestor].get('parent_id')
            if event == 'parent':
                try:
                    if any(states.get(child) != 'DESIGN_VALIDATED' for child in self.children[rid]):
                        states[rid] = 'SKIPPED'
                        self.design_warnings.append(
                            f'{rid}: skipped parent integration because one or more child designs were skipped')
                        self.log.info('PARENT_DESIGN_SKIPPED child design unavailable', node_id=rid)
                        continue
                    ir = self._compose_parent(ir, rid, nodes, database_schema, scenarios, dependencies)
                    states[rid] = 'DESIGN_VALIDATED'
                    write_json_atomic(self.root / 'backend_ir.json', ir)
                    self.log.info('PARENT_DESIGN_COMMITTED', node_id=rid)
                except ValueError as exc:
                    states[rid] = 'SKIPPED'
                    self.design_warnings.append(f'{rid}: skipped parent integration after {self.retries} correction opportunities: {exc}')
                    self.log.info(f'PARENT_DESIGN_SKIPPED {exc}', node_id=rid)
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
                # _design_layers works on a private trial. Discarding it leaves the
                # committed IR valid, so an unrepairable requirement is isolated.
                states[rid] = 'SKIPPED'
                self.design_warnings.append(f'{rid}: skipped after {self.retries} correction opportunities: {exc}')
                self.log.info(f'REQUIREMENT_SKIPPED {exc}', node_id=rid)
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
        errors = graph_errors(trial) + semantic_type_errors(trial) + self._lowering_preflight(trial, schema)
        if errors:
            raise ValueError('; '.join(errors))
        for key in reachable(api_ids, modules):
            modules[key]['requirement_ids'] = sorted(set(modules[key]['requirement_ids']) | {rid})
        return trial

    def _lowering_preflight(self, ir: dict[str, Any], schema: dict[str, Any]) -> list[str]:
        """Render in memory only: no source files or artifact registries are published."""
        if self.lowering_context is None or graph_errors(ir):
            return []
        from ..backend_lowering.stage import BackendLowerer
        project, database, output_root = self.lowering_context
        result = BackendLowerer().lower(ir, schema, project, database, output_root)
        return [f'Lowering preflight: {error}. Correct the responsible design contract before committing.'
                for error in result.errors]

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
        upstream = [{**module_contract(modules[key]),
                     'calls': copy.deepcopy(modules[key].get('calls', []))}
                    for key in sorted(upstream_ids)]
        candidates = requirement_reuse_candidates(base, rid, self.requirement_nodes, schema, kinds=kinds)
        # Accepted upper-layer modules occur once in upstream_modules, never again in reuse_context.
        candidates = [row for row in candidates if row['id'] not in upstream_ids]
        tables = project_design_context(schema, rid, dependencies)['tables'] if kind == 'DB' else []
        requested_types: set[str] = set()
        conflict_index = ModuleConflictIndex(base['modules'])
        for expansion in range(4):
            reuse = reuse_context(base, candidates, requirement, requested_types, kinds=kinds, upstream=upstream)
            payload = {'requirement': requirement}
            payload['semantic_types'] = semantic_type_summary(base)
            if self.repair_context:
                payload['repair_context'] = self.repair_context
            if kind == 'API':
                payload['route_index'] = [{'id': row['id'], 'method': row['method'], 'path': row['path']}
                                          for row in modules.values() if row['kind'] == 'API']
            reuse_payload = {key: value for key, value in reuse.items() if value}
            if reuse_payload:
                payload['reuse_context'] = reuse_payload
            rules = [{'id': row['requirement_id'], 'description': row['requirement'].get('description', '')}
                     for row in self.inherited_context if row['requirement'].get('description')]
            if rules:
                payload['ancestor_rules'] = rules
            if upstream:
                payload['upstream_modules'] = upstream
            if kind == 'DB':
                payload['schema_slice'] = {'tables': tables}
                loaded_tables = {row['name'] for row in tables}
                schema_index = [{'name': row['name'], 'description': row.get('description', '')}
                                for row in schema['tables'] if row['name'] not in loaded_tables]
                if schema_index:
                    payload['schema_index'] = schema_index

            loaded_modules = {row['id'] for row in candidates}
            known = {'requested_module_ids': {key for key, row in modules.items() if row['kind'] in kinds},
                     'requested_type_ids': {row['id'] for row in base['types']},
                     'requested_tables': {row['name'] for row in schema['tables']}}
            loaded = {'requested_module_ids': loaded_modules | upstream_ids,
                      'requested_type_ids': {row['id'] for row in reuse['types']},
                      'requested_tables': {row['name'] for row in tables}}

            def validate(value: dict[str, Any]) -> list[str]:
                errors = context_request_errors(value, known=known, loaded=loaded, expansions_remaining=3 - expansion)
                if errors or any(value.get(field) for field in known):
                    return errors
                updates = value.get('updated_modules', [])
                migrations = value.get('semantic_type_changes', {})
                if updates and not migrations and not self.repair_context:
                    return ['updated_modules requires explicit semantic_type_changes or a confirmed coverage repair.']
                if migrations:
                    # A migration can span layers; expose affected contracts before checking revisions.
                    affected_modules = [copy.deepcopy(row) for row in base['modules']
                                        if set(migrations) & semantic_type_registry(
                                            {'modules': [row], 'types': base['types']}).keys()]
                    payload['semantic_conflict_contracts'] = affected_modules
                    payload['semantic_conflict_types'] = required_types(affected_modules, base['types'])
                    newly_loaded = {row['id'] for row in affected_modules} - loaded['requested_module_ids']
                    loaded['requested_module_ids'].update(row['id'] for row in affected_modules)
                    loaded['requested_type_ids'].update(row['id'] for row in payload['semantic_conflict_types'])
                    if newly_loaded:
                        return [f'Type migration requires reviewing all affected contracts first. '
                                f'Now loaded in semantic_conflict_contracts: {sorted(newly_loaded)}. '
                                'Return the complete atomic revision using these exact contracts.']
                updated_ids = {row['id'] for row in updates}
                if len(updated_ids) != len(updates):
                    return ['updated_modules contains duplicate IDs.']
                invalid_updates = {row['id'] for row in updates if row['id'] not in loaded['requested_module_ids']
                                   or (not migrations and modules.get(row['id'], {}).get('kind') != kind)
                                   or (row['id'] in modules and modules[row['id']]['kind'] != row['kind'])}
                if invalid_updates:
                    return [f'Only loaded existing {kind} contracts can be revised: {sorted(invalid_updates)}. Request them first.']
                for row in updates:
                    previous = modules[row['id']]
                    if row['kind'] == 'API' and (row['method'], row['path']) != (previous['method'], previous['path']):
                        return [f"Revision {row['id']} must preserve its method and route; design a separate operation only when necessary."]
                    if migrations and not self.repair_context:
                        def stable(value: Any) -> Any:
                            if isinstance(value, list):
                                return [stable(item) for item in value]
                            if not isinstance(value, dict):
                                return value
                            return {key: ('declared migration' if key == 'type' and value.get('semantic_id') in migrations
                                          else 'named reference' if key == 'type_id' and value.get('kind') == 'named'
                                          else stable(item)) for key, item in value.items()}
                        if stable(row) != stable(previous):
                            return [f'Type migration {row["id"]} must preserve unrelated fields, behavior, ownership and calls.']
                combined = {**value, 'new_modules': [*value['new_modules'], *updates]}
                index = ModuleConflictIndex([row for row in base['modules'] if row['id'] not in updated_ids]) if updates else conflict_index
                conflicts = index.conflicts(combined)
                conflict_errors = index.errors(combined, conflicts)
                if conflicts:
                    contracts = {row['existing']['id']: module_contract(row['existing']) for row in conflicts}
                    payload['conflict_candidates'] = list(contracts.values())
                    payload['conflict_matches'] = [{key: item for key, item in row.items() if key != 'existing'} for row in conflicts]
                    payload['conflict_types'] = required_types(list(contracts.values()), base['types'])
                    for key in contracts.keys() - loaded['requested_module_ids']:
                        candidates.append(copy.deepcopy(modules[key]))
                        loaded['requested_module_ids'].add(key)
                    loaded['requested_type_ids'].update(row['id'] for row in payload['conflict_types'])
                new_ids = {row['id'] for row in value['new_modules']}
                for key in value.get('api_ids', []):
                    if key not in new_ids and (key not in loaded['requested_module_ids'] or modules.get(key, {}).get('kind') != 'API'):
                        conflict_errors.append(f"api_ids must select new APIs or loaded existing APIs: {key}. Request its contract first.")
                pending_ids = {row['module_id'] for row in pending}
                for source, target in value.get('resolved_calls', {}).items():
                    if source not in pending_ids:
                        conflict_errors.append(f"resolved_calls source is not an unresolved upstream call: {source}")
                    if target not in loaded['requested_module_ids'] or modules.get(target, {}).get('kind') != kind:
                        conflict_errors.append(f"resolved_calls target must be a loaded existing {kind} module: {target}")
                conflict_errors.extend(layer_reference_errors(value, rid=rid, kind=kind, base=base, loaded=loaded))
                if updates:
                    reference_updates = []
                    for row in updates:
                        previous = modules[row['id']]
                        reference_updates.append({**row,
                            'calls': [call for call in row.get('calls', []) if call not in previous.get('calls', [])],
                            **({'data_access': [access for access in row['data_access']
                                                if access not in previous.get('data_access', [])]}
                               if row['kind'] == 'DB' else {})})
                    conflict_errors.extend(layer_reference_errors({**value, 'new_modules': reference_updates}, rid=rid, kind=kind,
                                           base=base, loaded=loaded, updated_ids=updated_ids))
                if not conflict_errors:
                    candidate, entries = self._layer_candidate(base, rid, value, api_ids)
                    conflict_errors.extend(graph_errors(candidate, forward_kind={'API': 'FUNC', 'FUNC': 'DB', 'DB': None}[kind]))
                    conflict_errors.extend(contract_errors(candidate, schema))
                    semantic_errors = semantic_type_errors(candidate)
                    conflict_errors.extend(semantic_errors)
                    conflict_errors.extend(semantic_revision_errors(base, candidate, value))
                    if not conflict_errors:
                        conflict_errors.extend(self._lowering_preflight(candidate, schema))
                    if semantic_errors or migrations:
                        registry = semantic_type_registry(candidate)
                        affected = set(migrations)
                        affected.update(key for key, rows in registry.items()
                                        if len({json.dumps(row['signature'], sort_keys=True) for row in rows}) > 1)
                        owners = {row['owner_id'] for key in affected for row in registry.get(key, [])}
                        # Include modules referencing nested definitions, not just the definition itself.
                        owners.update(row['id'] for row in base['modules']
                                      if affected & semantic_type_registry({'modules': [row], 'types': base['types']}).keys())
                        existing = [copy.deepcopy(row) for row in base['modules'] if row['id'] in owners]
                        payload['semantic_conflict_contracts'] = existing
                        payload['semantic_conflict_types'] = required_types(existing, base['types'])
                        loaded['requested_module_ids'].update(row['id'] for row in existing)
                        loaded['requested_type_ids'].update(row['id'] for row in payload['semantic_conflict_types'])
                    candidate_modules = {row['id']: row for row in candidate['modules']}
                    used = reachable(entries, candidate_modules)
                    unused = {row['id'] if '::' in row['id'] else f"{rid}::{row['id']}"
                              for row in value['new_modules']} - used
                    if unused:
                        conflict_errors.append(f"New modules are unreachable from selected APIs: {sorted(unused)}. "
                                               "Connect needed responsibilities through calls or remove unused modules.")
                    for previous in base['modules']:
                        if previous['id'] not in updated_ids and rid not in previous['requirement_ids'] and candidate_modules[previous['id']] != previous:
                            conflict_errors.append(f"Reused module {previous['id']} was modified; reuse must leave its contract unchanged.")
                return conflict_errors

            layer_schema = copy.deepcopy(LAYER_SCHEMAS[kind])
            instructions = LAYER_PROMPTS[kind]
            if self.repair_context:
                instructions += ('\nRepair only the confirmed missing behavior. Prefer unchanged reuse. If a loaded existing '
                                 'contract is insufficient, updated_modules may explicitly revise that contract using its exact '
                                 'ID, kind and original ownership. Preserve unrelated behavior and callers; use new named type IDs '
                                 'for changed types. A revision is not reuse. Do not recreate the same operation under another route. '
                                 'Revised API behavior must delegate missing business/persistence work to appropriate new FUNC calls, '
                                 'rather than merely claiming support in its spec while retaining insufficient downstream contracts. '
                                 'New downstream calls still follow API -> FUNC -> DB. Updated API entries are selected automatically.\n')
            decision = self._decision('design_' + kind.lower(), rid, instructions,
                                      layer_schema, payload,
                                      validate=validate)
            requested_module_ids = set(decision.get('requested_module_ids', []))
            requested_type_ids = set(decision.get('requested_type_ids', []))
            fresh_modules = requested_module_ids & {key for key, row in modules.items() if row['kind'] in kinds} - loaded_modules - upstream_ids
            fresh_types = set(decision.get('requested_type_ids', [])) & {row['id'] for row in base['types']} - {row['id'] for row in reuse['types']}
            fresh_tables = set(decision.get('requested_tables', [])) & {row['name'] for row in schema['tables']} - {row['name'] for row in tables}
            requests = any(decision.get(key) for key in ('requested_module_ids', 'requested_type_ids', 'requested_tables'))
            if not requests:
                return self._layer_candidate(base, rid, decision, api_ids)
            requested_types.update(fresh_types)
            requested_modules = fresh_modules
            candidates.extend(copy.deepcopy(modules[key]) for key in sorted(requested_modules))
            requested_tables = fresh_tables
            tables.extend(copy.deepcopy(row) for row in schema['tables'] if row['name'] in requested_tables)
        raise ValueError('Layer context expansion budget exceeded')

    @staticmethod
    def _layer_candidate(base: dict[str, Any], rid: str, value: dict[str, Any],
                         api_ids: list[str]) -> tuple[dict[str, Any], list[str]]:
        updates = {row['id']: row for row in value.get('updated_modules', [])}
        filtered = {**base, 'modules': [row for row in base['modules'] if row['id'] not in updates]}
        merged = {**value, 'new_modules': [*value['new_modules'], *updates.values()]}
        if any(row['kind'] == 'API' for row in merged['new_modules']) or 'api_ids' in value:
            merged['api_ids'] = list(dict.fromkeys([*value.get('api_ids', api_ids),
                                  *[row['id'] for row in merged['new_modules'] if row['kind'] == 'API']]))
        trial, entries = BackendDesignPass._materialize_layer(filtered, rid, merged, api_ids)
        ownership = {row['id']: row['requirement_ids'] for row in base['modules'] if row['id'] in updates}
        for row in trial['modules']:
            if row['id'] in ownership:
                row['requirement_ids'] = sorted(set(ownership[row['id']]) | {rid})
        return trial, entries

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
        for row in value.get('new_types', []):
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
                       for source, target in value.get('resolved_calls', {}).items()}
        for module in modules.values():
            for call in module.get('calls', []):
                call['module_id'] = resolutions.get(call['module_id'], call['module_id'])
        entries = [mapping.get(key, qualify(key)) for key in value.get('api_ids', api_ids)]
        trial['types'], trial['modules'] = list(types.values()), list(modules.values())
        return trial, entries

    def _compose_parent(self, base: dict[str, Any], rid: str, nodes: dict[str, Any], schema: dict[str, Any],
                        scenarios: set[str], dependencies: dict[str, Any]) -> dict[str, Any]:
        trial = base
        repair_budget = _budget('ARC_BACKEND_COVERAGE_REPAIR_COUNT', 5, 5)
        repaired_gaps = []
        for attempt in range(repair_budget + 1):
            trial, gaps = self._review_parent(trial, rid, nodes, repaired_gaps)
            if not gaps:
                return trial
            if attempt == repair_budget:
                raise ValueError('Business coverage still incomplete after correction budget: '
                                 + '; '.join(f"{gap['capability']}: {gap['reason']}" for gap in gaps))
            original = self.requirement_nodes[rid]
            self.repair_context = {'confirmed_gaps': copy.deepcopy(gaps),
                                   'instruction': 'Repair these gaps only; preserve all completed behavior.'}
            focused = copy.deepcopy(original)
            focused['description'] = ('Complete the following confirmed backend design gaps for this requirement:\n'
                + '\n'.join(f"- {gap['capability']}: {gap['reason']}" for gap in gaps)
                + '\nOriginal requirement constraints:\n' + original.get('description', ''))
            self.requirement_nodes[rid] = focused
            try:
                self.log.info(f'COVERAGE_REPAIR_STARTED attempt={attempt + 1}/{repair_budget} gaps={len(gaps)}', node_id=rid)
                trial = self._design_layers(trial, rid, schema, dependencies, scenarios)
                repaired_gaps = gaps
            finally:
                self.requirement_nodes[rid] = original
                self.repair_context = None
        return trial

    def _review_parent(self, base: dict[str, Any], rid: str,
                       nodes: dict[str, Any], repaired_gaps: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
        available.update(key for key, module in modules.items() if module['kind'] == 'API'
                         and rid in module['requirement_ids'])
        related = requirement_reuse_candidates(base, rid, nodes, self.coverage_schema, kinds={'API'})
        available.update(row['id'] for row in related)
        all_apis = {key for key, module in modules.items() if module['kind'] == 'API'}
        def requirement_context(key: str) -> dict[str, Any]:
            return {field: copy.deepcopy(value) for field, value in nodes[key].items()
                    if field in {'id', 'name', 'description', 'scenarios'}}
        payload = {'requirement': requirement_context(rid),
                   'child_requirements': [requirement_context(key) for key in self.children[rid]],
                   'api_contracts': [module_contract(modules[key]) for key in sorted(available)]}
        if repaired_gaps:
            payload['repair_context'] = {'previous_gaps': copy.deepcopy(repaired_gaps),
                                       'instruction': 'Verify these gaps against the revised contracts and their collaborators.'}

        def collaborators() -> list[dict[str, Any]]:
            return [{'id': key, 'kind': modules[key]['kind'], 'spec': modules[key]['spec'][:800],
                     'calls': copy.deepcopy(modules[key].get('calls', [])),
                     **({'data_access': copy.deepcopy(modules[key]['data_access'])} if modules[key]['kind'] == 'DB' else {})}
                    for key in sorted(reachable(sorted(available), modules)) if modules[key]['kind'] != 'API']
        payload['collaborators'] = collaborators()
        payload['api_catalog'] = [{key: copy.deepcopy(modules[module_id][key])
                                  for key in ('id', 'method', 'path', 'authentication')}
                                  | {'spec': modules[module_id]['spec'][:240]}
                                 for module_id in sorted(all_apis - available)]
        if self.inherited_context:
            payload['ancestor_rules'] = [{'id': row['requirement_id'], 'description': row['requirement'].get('description', '')}
                                         for row in self.inherited_context if row['requirement'].get('description')]
        # Earlier branches' unresolved gaps are not evidence about this requirement.
        payload['types'] = required_types(payload, base['types'])

        def validate_parent(value: dict[str, Any]) -> list[str]:
            errors = []
            requests = set(value.get('requested_api_ids', []))
            if requests:
                if value['api_ids'] or value.get('pending_capabilities'):
                    errors.append('Context requests cannot include selected APIs or gaps; return api_ids:[] only.')
                if requests - all_apis or requests & available:
                    errors.append('Request exact unloaded IDs from api_catalog only.')
                if expansion >= 2:
                    errors.append('Coverage context expansion budget exhausted; evaluate loaded contracts.')
                return errors
            unknown = set(value['api_ids']) - available
            if unknown:
                errors.append(f"api_ids contains unavailable descendant APIs: {sorted(unknown)}. "
                              "Select exact IDs from api_contracts; report explicit missing server behavior separately.")
            if len(set(value['api_ids'])) != len(value['api_ids']):
                errors.append('api_ids must not contain duplicate entries.')
            gaps = value.get('pending_capabilities', [])
            if len({gap['id'] for gap in gaps}) != len(gaps):
                errors.append('pending_capabilities must have unique IDs.')
            existing_gaps = {gap['id'] for gap in self.pending_capabilities}
            for gap in gaps:
                if gap['id'] not in existing_gaps and not gap['id'].startswith(rid + '.capability.'):
                    errors.append(f"New capability {gap['id']} must use {rid}.capability.Name.")
                if gap['function_ids']:
                    errors.append(f"Backend capability {gap['id']} must have function_ids:[]; frontend functions are not designed yet.")
                if not gap['capability'].strip() or not gap['reason'].strip():
                    errors.append(f"Capability {gap['id']} needs explicit missing server behavior and a reason.")
            return errors

        for expansion in range(3):
            decision = self._decision('parent_composition', rid, PARENT_DESIGN_PROMPT,
                                      PARENT_DESIGN_SCHEMA, payload, validate=validate_parent)
            requested = set(decision.get('requested_api_ids', []))
            if not requested:
                break
            available.update(requested)
            payload['api_contracts'] = [module_contract(modules[key]) for key in sorted(available)]
            payload['types'] = required_types(payload['api_contracts'], base['types'])
            payload['collaborators'] = collaborators()
            payload['api_catalog'] = [row for row in payload['api_catalog'] if row['id'] not in available]
        trial = copy.deepcopy(base)
        targets = {row['id']: row for row in trial['modules']}
        for key in reachable(decision['api_ids'], targets):
            targets[key]['requirement_ids'] = sorted(set(targets[key]['requirement_ids']) | {rid})
        gaps = copy.deepcopy(decision.get('pending_capabilities', []))
        for gap in gaps:
            gap['requirement_ids'] = sorted(set(gap['requirement_ids']) | {rid})
        return trial, gaps

    def _finish(self, ir: dict[str, Any], states: dict[str, str], errors: list[str]) -> BackendDesignResult:
        errors = list(dict.fromkeys(errors))
        pending: dict[str, Any] = {}
        for source in self.pending_capabilities:
            gap = copy.deepcopy(source)
            prior = pending.get(gap['id'], {})
            gap['requirement_ids'] = sorted(set(prior.get('requirement_ids', [])) | set(gap['requirement_ids']))
            gap['function_ids'] = sorted(set(prior.get('function_ids', [])) | set(gap['function_ids']))
            pending[gap['id']] = gap
        warnings = list(self.design_warnings)
        warnings += [f"{gap['id']}: pending backend capability: {gap['capability']} ({gap['reason']})"
                    for gap in pending.values()]
        write_json_atomic(self.root / 'backend_ir.json', ir)
        write_json_atomic(self.root / 'requirement_contracts.json', requirement_contracts(ir, self.node_order))
        write_json_atomic(self.root / 'report.json', {'errors': errors, 'warnings': warnings,
                                                    'pending_capabilities': list(pending.values()),
                                                    'status': 'FAILED' if errors else 'GENERATED_WITH_WARNINGS' if warnings else 'GENERATED',
                                                    'model_calls': self.sequence,
                                                    'traversal': 'leaves_then_parent_coverage', 'node_order': self.node_order})
        self.log.info(f'BACKEND_DESIGN_FINISHED success={not errors} '
                      f'requirements={len(states)} modules={len(ir["modules"])} '
                      f'model_calls={self.sequence} errors={len(errors)} '
                      f'duration_seconds={time.perf_counter() - self.started_at:.1f} '
                      f'ir={self.root / "backend_ir.json"} report={self.root / "report.json"}')
        for error in errors:
            self.log.info(f'BACKEND_DESIGN_ERROR {error}')
        return BackendDesignResult(ir, states, errors, warnings)
