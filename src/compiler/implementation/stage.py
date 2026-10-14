"""Leaf TDD loop with direct editable scope, dynamic loading and frozen regression tests."""
from __future__ import annotations
import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from compiler.backend_design.validation import shape_errors
from compiler.preprocessing.traversal import children_by_id, postorder
from compiler.lowering.planning import slug, token
from model.client import describe_model_error
from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog
from .contracts import SCHEMA, PROMPTS
from .file_context import FileContext
from .patcher import apply_edits, read_source, restore
from .test_runner import TestRunner, passed


@dataclass
class TDDResult:
    node_states: dict[str, str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


def budget(name, default):
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


class NodeTDDOrchestrator:
    def __init__(self, model, output_root, *, runner=None, port=33100):
        self.model, self.root = model, Path(output_root).resolve()
        self.log = SynchronousLog('NodeTDDOrchestrator', workspace_root=self.root)
        self.artifact = self.root / '.arc/implementation'
        self.attempts = budget('MODEL_ATTEMPT', 5)
        self.iterations = budget('ARC_TDD_MAX_ITERATIONS', 5)
        self.load_rounds = budget('ARC_TDD_LOAD_ROUNDS', 12)
        self.runner = runner or TestRunner(self.root)
        self.port, self.sequence = port, 0
        if hasattr(model, 'set_usage_path'):
            model.set_usage_path(self.root / '.arc/model_usage.jsonl')

    def compile(self, requirements, database, backend, frontend, backend_plan, frontend_plan, database_plan):
        self.context = FileContext(self.root, requirements, database, backend, frontend,
                                   backend_plan, frontend_plan, database_plan)
        required_shared = {'backend/src/db/seed.ts', 'backend/src/db/initialize.ts', 'backend/src/db/client.ts'}
        missing = [path for path in required_shared if not (self.root / path).is_file()]
        if missing:
            raise ValueError('Run database lowering before TDD: missing ' + ', '.join(missing))
        self.runner.prepare()
        # Freeze runner-owned support/config as read-only discoverable records.
        for path in self.runner.support.glob('*'):
            relative = path.relative_to(self.root).as_posix()
            self.context.catalog[relative] = {'file_path': relative, 'symbol_ids': ['SYSTEM.TEST.' + path.name],
                'reqs': [], 'shared': True, 'summaries': [path.name + ': fixed TDD runner support'],
                'kind': 'configuration', 'contracts': []}
        baseline = self.runner.compile()
        if not passed(baseline):
            self._gate('BASELINE', 'compilation', baseline)
            return self._report({}, ['Baseline compilation failed before TDD'], [])
        children = children_by_id(requirements)
        order = [rid for rid in postorder(requirements) if not children[rid]]
        # Process leaf dependencies before dependents; never design/implement parent nodes.
        pending, scheduled, active = set(order), [], set()
        def visit(rid):
            if rid not in pending:
                return
            if rid in active:
                raise ValueError('Leaf implementation dependency cycle at ' + rid)
            active.add(rid)
            for dependency in requirements['nodes'][rid].get('dependencies', []):
                dependencies = [key for key in order if key == dependency or self._descends(key, dependency, requirements)]
                for key in dependencies:
                    if key != rid:
                        visit(key)
            active.remove(rid)
            pending.remove(rid)
            scheduled.append(rid)
        for rid in order:
            visit(rid)
        states, errors, warnings, completed_tests = {}, [], [], []
        self.test_manifest = {}
        for rid in scheduled:
            self.log.info('Leaf TDD: RUNNING', node_id=rid)
            if not self.context.direct_files(rid):
                states[rid] = 'SKIPPED_NO_DESIGN'
                warnings.append(f'{rid}: no associated lowered files; not treated as implemented')
                self.log.warning(warnings[-1], node_id=rid)
                continue
            loaded, snapshots = set(), {}
            evidence = []
            try:
                targets = self._targets(rid, backend, frontend)
                self.test_manifest[rid] = targets
                for path in targets:
                    self.context.catalog[path] = {'file_path': path, 'symbol_ids': ['TEST.' + path],
                        'reqs': [rid], 'shared': False, 'summaries': [rid + ' frozen acceptance tests'],
                        'kind': 'acceptance_test', 'contracts': []}
                for test_attempt in range(self.attempts):
                    self._model_patch('test_generation', rid, loaded, targets, evidence, snapshots,
                                      accept=lambda: self._test_generation_gate(targets))
                    red = self._run_tests(targets, rid, f'red_{test_attempt + 1}', red=True)
                    self._gate(rid, f'red_{test_attempt + 1}', red)
                    if passed(red) or self._behavior_failure(red):
                        break
                    if any(row.get('failure_kind') not in {'test_code', 'behavior'} for row in red if row['status'] != 'PASSED'):
                        raise ValueError('Red gate infrastructure/application failure; no test regeneration attempted: ' + str(red))
                    evidence = [row for row in red if row.get('failure_kind') == 'test_code']
                if passed(red):
                    states[rid] = 'ALREADY_PASSED'
                    completed_tests.extend(targets)
                    continue
                if not self._behavior_failure(red):
                    raise ValueError('Red gate failed because tests/application could not run, not an observed assertion failure')
                evidence = red
                success = False
                for iteration in range(1, self.iterations + 1):
                    phase = 'implementation' if iteration == 1 else 'repair'
                    self._model_patch(phase, rid, loaded, targets, evidence, snapshots,
                                      accept=lambda: self.runner.compile() + self.runner.check_tests(completed_tests + targets))
                    # Previously passed requirements remain a regression gate after every source edit.
                    evidence = self._run_tests(list(dict.fromkeys(completed_tests + targets)), rid, f'green_{iteration}')
                    self._gate(rid, f'green_{iteration}', evidence)
                    if passed(evidence):
                        success = True
                        break
                    if any(row.get('failure_kind') == 'infrastructure' for row in evidence if row['status'] != 'PASSED'):
                        raise ValueError('Test infrastructure failed after retries; source repair cannot fix the executor: ' + str(evidence))
                if not success:
                    raise ValueError(f'Acceptance/regression tests still fail after {self.iterations} implementation rounds')
                states[rid] = 'IMPLEMENTED'
                self.log.info('Leaf TDD: COMPLETED', node_id=rid)
                completed_tests.extend(targets)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                restore(self.root, snapshots)
                states[rid] = 'FAILED_ROLLED_BACK'
                errors.append(f'{rid}: {exc}')
                self.log.error(errors[-1], node_id=rid)
            self._report(states, errors, warnings)
        final = self.runner.compile()
        self._gate('FINAL', 'compilation', final)
        if not passed(final):
            errors.append('Final compilation failed')
        if completed_tests:
            regression = self._run_tests(list(dict.fromkeys(completed_tests)), 'FINAL', 'regression')
            self._gate('FINAL', 'regression', regression)
            if not passed(regression):
                errors.append('Final acceptance regression failed')
        return self._report(states, errors, warnings)

    @staticmethod
    def _descends(rid, parent, requirements):
        current = requirements['nodes'][rid].get('parent_id')
        seen = set()
        while current in requirements['nodes'] and current not in seen:
            if current == parent:
                return True
            seen.add(current)
            current = requirements['nodes'][current].get('parent_id')
        return False

    def _run_tests(self, targets, rid, phase, *, red=False):
        """Retry infrastructure without asking the model to rewrite valid tests."""
        attempts = budget('ARC_TDD_RUN_ATTEMPTS', 2)
        for attempt in range(1, attempts + 1):
            results = self.runner.run(targets, port=self.port, red=red)
            self._gate(rid, f'{phase}_run_{attempt}', results)
            if not any(row.get('failure_kind') == 'infrastructure' for row in results if row['status'] != 'PASSED'):
                return results
            self.log.warning(f'{phase}: infrastructure failure, executor retry {attempt}/{attempts}; keeping tests unchanged', node_id=rid)
        return results

    @staticmethod
    def _targets(rid, backend, frontend):
        stem = slug(rid) + '-' + token(rid)
        kinds = {row['kind'] for row in backend['modules'] if rid in row['reqs']}
        targets = []
        if 'FUNC' in kinds:
            targets.append(f'tests/unit/{stem}.test.ts')
        if kinds & {'API', 'DB'}:
            targets.append(f'tests/integration/{stem}.test.ts')
        nodes, _ = ui_index_local(frontend)
        if any(rid in row['reqs'] for row in nodes.values()):
            targets.append(f'tests/e2e/{stem}.spec.ts')
        if not targets:
            raise ValueError(f'No appropriate acceptance layer for {rid}')
        return targets

    def _test_generation_gate(self, targets):
        for path in targets:
            content = read_source(self.root, path)
            if not content.strip() or not re.search(r'\bexpect\s*\(', content):
                raise ValueError(f'Acceptance assertions required in {path}')
            if not re.search(r'\b(?:test|it)\s*\(', content):
                raise ValueError(f'Runnable acceptance test declaration required in {path}')
            if re.search(r'\b(?:test|it|describe)\.(?:skip|todo|fixme|only)\b', content):
                raise ValueError(f'Acceptance tests cannot skip or focus cases: {path}')
        return self.runner.check_tests(targets)

    @staticmethod
    def _behavior_failure(results):
        # Compiler failures cannot become RED just because their log mentions an assertion.
        failures = [row for row in results if row['status'] != 'PASSED']
        return bool(failures) and all(row.get('test_evidence', {}).get('behavior_failure', False) for row in failures)

    def _model_patch(self, phase, rid, loaded, targets, evidence, snapshots, accept):
        feedback, previous, requests = [], None, 0
        for attempt in range(self.attempts):
            self.log.info(f'{phase} attempt {attempt + 1}/{self.attempts}', node_id=rid)
            while True:
                payload = self.context.build(rid, phase, loaded, targets, evidence)
                if feedback:
                    payload['validation_feedback'], payload['previous_output'] = feedback, previous
                self.sequence += 1
                trace = {'requirement_id': rid, 'phase': phase, 'attempt': attempt + 1,
                         'instructions': PROMPTS[phase], 'input': payload}
                patch_snapshot = {}
                try:
                    output = self.model.generate_json(schema_name='tdd_' + phase, instructions=PROMPTS[phase],
                                                       input_payload=payload, output_schema=SCHEMA)
                    previous = output
                    trace['output'] = output
                    # A known transport metadata echo carries no application semantics.
                    # Preserve the raw trace; all other unexpected fields remain errors.
                    if isinstance(output, dict) and output.get('type') == 'json_object':
                        output = {key: value for key, value in output.items() if key != 'type'}
                        trace['protocol_normalization'] = 'Removed echoed type:json_object transport metadata'
                        self.log.warning(trace['protocol_normalization'], node_id=rid)
                    validation = shape_errors(output, SCHEMA)
                    if validation:
                        raise ValueError('; '.join(validation))
                    if output['blocked_reason'].strip():
                        raise ValueError(output['blocked_reason'])
                    if output['request_files']:
                        if output['edits']:
                            raise ValueError('request_files cannot be combined with edits')
                        paths = self.context.resolve_requests(output['request_files'], targets)
                        if not paths - loaded - set(payload['always_loaded_files']):
                            raise ValueError('Requested files are already loaded')
                        requests += 1
                        if requests > self.load_rounds:
                            raise ValueError('File loading rounds exhausted; choose minimal necessary files')
                        loaded.update(paths)
                        self.log.info('Loading requested files: ' + ', '.join(sorted(paths)), node_id=rid)
                        trace['errors'] = []
                        self._trace(trace)
                        continue
                    if not output['edits']:
                        raise ValueError('Provide implementation/test edits; no-op response cannot satisfy this task')
                    patch_snapshot = apply_edits(self.root, output['edits'], set(payload['editable_files']),
                                                 ignore_unchanged=phase == 'test_generation')
                    gate = accept()
                    if not passed(gate):
                        raise ValueError('Compilation/test-source gate failed: ' + str(gate))
                    for path, content in patch_snapshot.items():
                        snapshots.setdefault(path, content)
                    trace['errors'] = []
                    trace['gate'] = gate
                    self._trace(trace)
                    return
                except Exception as exc:
                    if patch_snapshot:
                        restore(self.root, patch_snapshot)
                    feedback = [describe_model_error(exc)]
                    self.log.warning(f'{phase} rejected: ' + '; '.join(feedback), node_id=rid)
                    trace['errors'] = feedback
                    self._trace(trace)
                    break
        raise ValueError(phase + ': exhausted correction attempts: ' + '; '.join(feedback))

    def _trace(self, trace):
        write_json_atomic(self.artifact / 'passes' / f'{self.sequence:05d}.json', trace)

    def _gate(self, rid, phase, results):
        self.log.info(f'{phase}: ' + ('PASSED' if passed(results) else 'FAILED'), node_id=rid)
        write_json_atomic(self.artifact / 'gates' / f'{slug(rid)}-{phase}.json', results)

    def _report(self, states, errors, warnings):
        write_json_atomic(self.artifact / 'report.json', {'status': 'FAILED' if errors else 'IMPLEMENTED_WITH_WARNINGS' if warnings else 'IMPLEMENTED',
            'node_states': states, 'errors': errors, 'warnings': warnings, 'model_calls': self.sequence})
        if hasattr(self, 'test_manifest'):
            write_json_atomic(self.artifact / 'test_manifest.json', self.test_manifest)
        return TDDResult(copy.deepcopy(states), list(errors), list(warnings))


def ui_index_local(frontend):
    from compiler.frontend_design.validation import ui_index
    return ui_index(frontend['ui'])
