"""Leaf TDD loop with direct editable scope and requirement-grounded test repair."""
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
from .working_context import WorkingContext
from .patcher import apply_edits, read_source, restore, source_versions
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


class EnvironmentBlocked(RuntimeError):
    """Executor failures preserve accepted edits and stop this implementation run."""


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
        self.test_status = {}
        if hasattr(model, 'set_usage_path'):
            model.set_usage_path(self.root / '.arc/model_usage.jsonl')

    def compile(self, requirements, database, backend, frontend, backend_plan, frontend_plan, database_plan,
                *, skip_requirements=()):
        try:
            return self._compile(requirements, database, backend, frontend, backend_plan, frontend_plan,
                                 database_plan, skip_requirements=skip_requirements)
        finally:
            if hasattr(self.runner, 'close'):
                self.runner.close()

    def _compile(self, requirements, database, backend, frontend, backend_plan, frontend_plan, database_plan,
                 *, skip_requirements=()):
        self.context = FileContext(self.root, requirements, database, backend, frontend,
                                   backend_plan, frontend_plan, database_plan)
        required_shared = {'backend/src/db/seed.ts', 'backend/src/db/initialize.ts', 'backend/src/db/client.ts'}
        missing = [path for path in required_shared if not (self.root / path).is_file()]
        if missing:
            raise ValueError('Run database lowering before TDD: missing ' + ', '.join(missing))
        self.runner.prepare()
        children = children_by_id(requirements)
        order = [rid for rid in postorder(requirements) if not children[rid]]
        needs_browser = bool(frontend['ui']['children']) or any(
            'frontend' in layers for layers in self.context.recovery.values())
        environment = self.runner.check_environment(browser=needs_browser)
        self._gate('BASELINE', 'environment', environment)
        if not passed(environment):
            return self._report({rid: 'BLOCKED_ENVIRONMENT' for rid in order},
                                ['Test environment unavailable before TDD: ' + str(environment)], [])
        # Freeze runner-owned support/config as read-only discoverable records.
        for path in self.runner.support.glob('*'):
            relative = path.relative_to(self.root).as_posix()
            self.context.catalog[relative] = {'file_path': relative, 'symbol_ids': ['SYSTEM.TEST.' + path.name],
                'reqs': [], 'shared': True, 'summaries': [path.name + ': fixed TDD runner support'],
                'kind': 'configuration', 'contracts': []}
        baseline = self.runner.compile()
        if not passed(baseline):
            self._gate('BASELINE', 'compilation', baseline)
            self.log.warning('Baseline compilation needs repair; continuing with requirement recovery files.')
        baseline_needs_repair = not passed(baseline)
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
        skipped = set(skip_requirements)
        for rid in scheduled:
            if rid in skipped:
                states[rid] = 'SKIPPED_DESIGN_FAILED'
                warnings.append(f'{rid}: design attempts exhausted; skipping TDD')
                self.log.warning(warnings[-1], node_id=rid)
                continue
            self.log.info('Leaf TDD: RUNNING', node_id=rid)
            if not self.context.direct_files(rid):
                states[rid] = 'SKIPPED_NO_DESIGN'
                warnings.append(f'{rid}: no associated lowered files; not treated as implemented')
                self.log.warning(warnings[-1], node_id=rid)
                continue
            session, snapshots = WorkingContext(), {}
            evidence, targets = [], []
            repaired_baseline = False
            try:
                if baseline_needs_repair:
                    self._model_patch('repair', rid, session, [], baseline, snapshots,
                                      accept=lambda: self.runner.compile())
                    baseline_needs_repair = False
                    repaired_baseline = True
                targets = self._targets(rid, backend, frontend, recovery=self.context.recovery.get(rid, {}))
                self.test_manifest[rid] = targets
                for path in targets:
                    self.context.catalog[path] = {'file_path': path, 'symbol_ids': ['TEST.' + path],
                        'reqs': [rid], 'shared': False, 'summaries': [rid + ' requirement-based acceptance tests'],
                        'kind': 'acceptance_test', 'contracts': []}
                self._model_patch('test_generation', rid, session, targets, evidence, snapshots,
                                  accept=lambda: self._test_generation_gate(targets))
                self.log.info('Acceptance tests compiled; implementing initial version before execution. Test corrections are permitted during repair.', node_id=rid)
                direct = self.context.direct_files(rid)
                for layer in ('backend', 'frontend'):
                    if not any(path.startswith(layer + '/') for path in direct):
                        continue
                    self._model_patch(layer + '_implementation', rid, session, targets, [], snapshots,
                                      accept=lambda: self.runner.compile() + self.runner.check_tests(completed_tests + targets))
                # Execute only this requirement; historical test sources remain
                # covered by the compilation gates above and during repair.
                evidence = self._run_tests(targets, rid, 'initial_implementation')
                self._gate(rid, 'initial_implementation', evidence)
                success = passed(evidence)
                for iteration in range(1, self.iterations + 1):
                    if success:
                        break
                    if any(row.get('failure_kind') == 'infrastructure' for row in evidence if row['status'] != 'PASSED'):
                        raise EnvironmentBlocked('Test executor unavailable after retries; preserving accepted source: ' + str(evidence))
                    self._model_patch('repair', rid, session, targets, evidence, snapshots,
                                      accept=lambda: self.runner.compile() + self.runner.check_tests(completed_tests + targets))
                    evidence = self._run_tests(targets, rid, f'repair_{iteration}')
                    self._gate(rid, f'repair_{iteration}', evidence)
                    if passed(evidence):
                        success = True
                        break
                    if any(row.get('failure_kind') == 'infrastructure' for row in evidence if row['status'] != 'PASSED'):
                        raise EnvironmentBlocked('Test executor unavailable after retries; preserving accepted source: ' + str(evidence))
                if not success:
                    raise ValueError(f'Current requirement acceptance tests still fail after initial implementation and {self.iterations} repair rounds')
                states[rid] = 'IMPLEMENTED'
                self.log.info('Leaf TDD: COMPLETED', node_id=rid)
                completed_tests.extend(targets)
                self._report(states, errors, warnings)
            except EnvironmentBlocked as exc:
                states[rid] = 'BLOCKED_ENVIRONMENT'
                for remaining in scheduled:
                    states.setdefault(remaining, 'BLOCKED_ENVIRONMENT')
                errors.append(f'{rid}: {exc}')
                self.log.error(errors[-1], node_id=rid)
                return self._report(states, errors, warnings)
            except Exception as exc:
                # Runtime acceptance failure does not discard compilation-safe
                # progress. Individual invalid proposals are still reverted by
                # _model_patch before reaching this requirement boundary.
                retained_tests = [path for path in targets if (self.root / path).is_file()]
                try:
                    compilation = self.runner.compile()
                    test_sources = list(dict.fromkeys(completed_tests + retained_tests))
                    if test_sources:
                        compilation += self.runner.check_tests(test_sources)
                    self._gate(rid, 'failure_compilation', compilation)
                    keep_source = passed(compilation)
                except Exception as compilation_exc:
                    keep_source = False
                    warnings.append(f'{rid}: could not verify compilation before retaining source: {compilation_exc}')
                if keep_source:
                    states[rid] = 'FAILED_RETAINED'
                    # Include retained tests in subsequent compilation gates;
                    # they are not scheduled for regression execution.
                    completed_tests.extend(retained_tests)
                    warnings.append(f'{rid}: keeping last compilation-safe result; acceptance remains incomplete')
                    self.log.warning(warnings[-1], node_id=rid)
                else:
                    try:
                        restore(self.root, snapshots)
                    except Exception as rollback_exc:
                        errors.append(f'{rid}: rollback failed: {rollback_exc}')
                    baseline_needs_repair = baseline_needs_repair or repaired_baseline
                    states[rid] = 'FAILED_ROLLED_BACK'
                errors.append(f'{rid}: {exc}')
                self.log.error(errors[-1], node_id=rid)
            self._report(states, errors, warnings)
        final = self.runner.compile()
        if completed_tests:
            final += self.runner.check_tests(list(dict.fromkeys(completed_tests)))
        self._gate('FINAL', 'compilation', final)
        if not passed(final):
            errors.append('Final compilation failed')
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
    def _targets(rid, backend, frontend, *, recovery=None):
        stem = slug(rid) + '-' + token(rid)
        kinds = {row['kind'] for row in backend['modules'] if rid in row['reqs']}
        targets = []
        if 'FUNC' in kinds:
            targets.append(f'tests/unit/{stem}.test.ts')
        if kinds & {'API', 'DB'} or recovery and 'backend' in recovery:
            targets.append(f'tests/integration/{stem}.test.ts')
        nodes, _ = ui_index_local(frontend)
        if any(rid in row['reqs'] for row in nodes.values()) or recovery and 'frontend' in recovery:
            targets.append(f'tests/e2e/{stem}.spec.ts')
        if not targets:
            raise ValueError(f'No appropriate acceptance layer for {rid}')
        return targets

    def _test_generation_gate(self, targets):
        self._validate_test_sources(targets)
        return self.runner.check_tests(targets)

    def _validate_test_sources(self, targets):
        for path in targets:
            content = read_source(self.root, path)
            if not content.strip() or not re.search(r'\bexpect\s*\(', content):
                raise ValueError(f'Acceptance assertions required in {path}')
            if not re.search(r'\b(?:test|it)\s*\(', content):
                raise ValueError(f'Runnable acceptance test declaration required in {path}')
            if re.search(r'\b(?:test|it|describe)\.(?:skip|todo|fixme|only)\b', content):
                raise ValueError(f'Acceptance tests cannot skip or focus cases: {path}')
            # Reject explicit positive assertions of generated placeholders, not legitimate
            # business error assertions or negative assertions that placeholders are absent.
            if (re.search(r'(?<!\.not)\.(?:toThrow|toThrowError)\s*\(\s*(?:[\'\"]Not[ -]implemented|NotImplementedError\b)', content, re.I)
                    or re.search(r'(?<!\.not)\.(?:toBe|toEqual|toStrictEqual)\s*\(\s*501\s*\)', content)
                    or re.search(r'\.expect\s*\(\s*501\s*\)', content)):
                raise ValueError(f'{path}: assert the requirement\'s implemented behavior, not Not implemented errors or placeholder HTTP 501')

    @staticmethod
    def _behavior_failure(results):
        # Compiler failures cannot become RED just because their log mentions an assertion.
        failures = [row for row in results if row['status'] != 'PASSED']
        return bool(failures) and all(row.get('test_evidence', {}).get('behavior_failure', False) for row in failures)

    def _model_patch(self, phase, rid, session, targets, evidence, snapshots, accept):
        feedback, rejected_files, requests = [], [], 0
        for attempt in range(self.attempts):
            self.log.info(f'{phase} attempt {attempt + 1}/{self.attempts}', node_id=rid)
            while True:
                payload = self.context.build(rid, phase, session, targets, evidence)
                expected_versions = source_versions(payload['editable_files'])
                if feedback:
                    payload['validation_feedback'] = feedback
                    payload['rejected_patch'] = {'installed': False, 'files': rejected_files}
                self.sequence += 1
                patch_snapshot = {}
                output = None
                try:
                    output = self.model.generate_json(schema_name='tdd_' + phase, instructions=PROMPTS[phase],
                                                       input_payload=payload, output_schema=SCHEMA)
                    # A known transport metadata echo carries no application semantics.
                    # The raw response remains in model_logs; other fields remain errors.
                    if isinstance(output, dict) and output.get('type') == 'json_object':
                        output = {key: value for key, value in output.items() if key != 'type'}
                        self.log.warning('Removed echoed type:json_object transport metadata', node_id=rid)
                    validation = shape_errors(output, SCHEMA)
                    if validation:
                        raise ValueError('; '.join(validation))
                    if output['blocked_reason'].strip():
                        raise ValueError(output['blocked_reason'])
                    if output['request_files'] or output.get('release_files'):
                        if output['edits']:
                            raise ValueError('Context read/release operations cannot be combined with edits')
                        paths = self.context.resolve_requests(output['request_files'], targets)
                        releases = self.context.resolve_requests(output.get('release_files', []), targets)
                        disallowed = {path for path in paths if not self.context.phase_path(path, phase)}
                        if disallowed:
                            raise ValueError(f'Files outside {phase} scope: {sorted(disallowed)}')
                        requests += 1
                        if requests > self.load_rounds:
                            raise ValueError('File loading rounds exhausted; choose minimal necessary files')
                        session.apply(output, paths, releases,
                                      set(payload['context_state']['pinned_files']), self.sequence)
                        self.log.info('Context read: ' + ', '.join(sorted(paths))
                                      + '; release: ' + ', '.join(sorted(releases)), node_id=rid)
                        continue
                    if not output['edits']:
                        raise ValueError('Provide implementation/test edits; no-op response cannot satisfy this task')
                    patch_snapshot = apply_edits(self.root, output['edits'], set(payload['editable_files']),
                                                 expected_versions=expected_versions,
                                                 ignore_unchanged=phase == 'test_generation')
                    changed_tests = [path for path in patch_snapshot
                                     if self.context.catalog.get(path, {}).get('kind') == 'acceptance_test']
                    if changed_tests:
                        self._validate_test_sources(changed_tests)
                    gate = accept()
                    if not passed(gate):
                        if any(row.get('failure_kind') == 'infrastructure' for row in gate
                               if row['status'] != 'PASSED'):
                            raise EnvironmentBlocked('Compilation executor unavailable; preserving previously accepted source: ' + str(gate))
                        raise ValueError('Compilation/test-source gate failed: ' + str(gate))
                    for path, content in patch_snapshot.items():
                        snapshots.setdefault(path, content)
                    session.touch(patch_snapshot)
                    return
                except EnvironmentBlocked:
                    # This proposal has not passed its gate; retain earlier accepted
                    # patches without installing the unvalidated proposal.
                    if patch_snapshot:
                        restore(self.root, patch_snapshot)
                    raise
                except Exception as exc:
                    if patch_snapshot:
                        restore(self.root, patch_snapshot)
                    feedback = [describe_model_error(exc)]
                    edits = output.get('edits', []) if isinstance(output, dict) else []
                    rejected_files = sorted({edit['file'] for edit in edits
                                             if isinstance(edit, dict) and isinstance(edit.get('file'), str)}) if isinstance(edits, list) else []
                    session.focus.update(path for path in rejected_files if path in payload['editable_files'])
                    self.log.warning(f'{phase} rejected: ' + '; '.join(feedback), node_id=rid)
                    break
        raise ValueError(phase + ': exhausted correction attempts: ' + '; '.join(feedback))

    def _gate(self, rid, phase, results):
        self.log.info(f'{phase}: ' + ('PASSED' if passed(results) else 'FAILED'), node_id=rid)
        self.test_status[rid] = {'phase': phase, 'results': [
            {key: row[key] for key in ('status', 'failure_kind', 'exit_code', 'error', 'test_files') if key in row}
            | ({'tests': {key: row['test_evidence'][key] for key in
                         ('collected', 'executed_files') if key in row['test_evidence']}
                         | {'failed_tests': [{key: test[key] for key in ('file', 'name', 'status', 'location') if key in test}
                                             for test in row['test_evidence'].get('failed_tests', [])]}}
               if 'test_evidence' in row else {}) for row in results]}
        write_json_atomic(self.artifact / 'test_status.json', self.test_status)

    def _report(self, states, errors, warnings):
        errors = [message[:1500] for message in errors]
        write_json_atomic(self.artifact / 'report.json', {'status': 'FAILED' if errors else 'IMPLEMENTED_WITH_WARNINGS' if warnings else 'IMPLEMENTED',
            'node_states': states, 'errors': errors, 'warnings': warnings, 'model_calls': self.sequence})
        if hasattr(self, 'test_manifest'):
            write_json_atomic(self.artifact / 'test_manifest.json', self.test_manifest)
        return TDDResult(copy.deepcopy(states), list(errors), list(warnings))


def ui_index_local(frontend):
    from compiler.frontend_design.validation import ui_index
    return ui_index(frontend['ui'])
