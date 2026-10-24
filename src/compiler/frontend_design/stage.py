"""Leaf-only UI -> data -> interaction design with atomic requirement rollback."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .presentation import frontend_metadata

from compiler.context.structured import DesignDecision
from compiler.preprocessing.queries import ancestor_ids, leaf_ids
from utils.jsonio import read_json, write_json_atomic
from utils.logging import SynchronousLog
from .context import select_types, expand_api_context
from .tasks import FrontendTaskInput, UIDesignTask, DataDesignTask, InteractionDesignTask
from .contracts import PROMPTS, SCHEMAS
from .delta import apply_ui, apply_data, apply_interaction, make_ui
from .validation import validate_ir


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
    def __init__(self, model, artifact_root: Path, *, visual_model=None, reference_root: Path | None = None,
                 visual_result=None):
        self.model = model
        self.visual_model = visual_model
        self.artifact_root = Path(artifact_root)
        self.root = self.artifact_root / 'design/frontend'
        self.log = SynchronousLog('FrontendDesignPass', workspace_root=self.artifact_root.parent)
        self.visual_log = SynchronousLog('VisualAnalysis', workspace_root=self.artifact_root.parent)
        self.reference_root = reference_root
        self.cache_path = self.artifact_root / 'visual_cache.json'
        self.visual_result = visual_result
        self.visual_cache = copy.deepcopy(visual_result.cache) if visual_result else read_json(self.cache_path, {})
        self.visual_failures = dict(visual_result.failures) if visual_result else {}
        self._visual_analyzer = None
        try:
            self.attempts = max(1, int(os.getenv('MODEL_ATTEMPT', '5')))
        except ValueError:
            self.attempts = 5
        self.sequence = visual_result.model_calls if visual_result else 0
        if hasattr(model, 'set_usage_path'):
            model.set_usage_path(self.artifact_root / 'model_usage.jsonl')

    def compile(self, requirements, database, backend):
        # Stage: Frontend design - validate input contracts ====================================
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
        order = leaf_ids(requirements)
        # Process: Design each leaf on transactional UI and page copies ========================
        for rid in order:
            self.log.info('Leaf design: RUNNING', node_id=rid)
            trial, trial_pages = copy.deepcopy(ir), copy.deepcopy(page_states)
            try:
                requirement = self.requirement(requirements['nodes'][rid], requirements['nodes'])
                if not requirement.get('pages'):
                    states[rid] = 'NO_FRONTEND'
                    self.log.info('No frontend page; skipping leaf.', node_id=rid)
                    continue
                # Process: Collect visual evidence for the current leaf ========================
                evidence = {}
                for page in requirement['pages']:
                    reference = page.get('reference')
                    if reference:
                        try:
                            if reference in self.visual_failures:
                                raise ValueError(self.visual_failures[reference])
                            record = self.analyze(page['name'], reference, rid)
                            evidence[reference] = record
                            page['analysis'] = record['analysis']
                        except Exception as exc:
                            self.visual_failures[reference] = str(exc)
                            page['analysis'] = None
                            self.visual_log.warning(f'Skipping {reference}: {exc}', node_id=rid)
                            warnings.append(f'{rid}: visual analysis unavailable for {reference}; using requirement evidence')
                    else:
                        known = next((row for row in trial_pages if row['name'] == page['name']), None)
                        page['analysis'] = self.page_analysis(known)
                inputs = FrontendTaskInput(rid, requirement, trial, trial_pages, database, backend, evidence)
                ui = UIDesignTask(self.design, self.page_analysis).run(inputs)
                requirement = ui.requirement
                inputs = FrontendTaskInput(rid, requirement, ui.candidate, ui.page_states, database, backend)
                data = DataDesignTask(self.design, self.types).run(inputs)
                inputs = FrontendTaskInput(rid, requirement, data.candidate, data.page_states, database, backend)
                interaction = InteractionDesignTask(self.design, self.types).run(inputs, data.context)
                trial, trial_pages = interaction.candidate, interaction.page_states
                self.save(trial, trial_pages)
                ir, page_states = trial, trial_pages
                states[rid] = 'DESIGNED'
                self.log.info('Leaf design: COMPLETED', node_id=rid)
            except Exception as exc:
                states[rid] = 'SKIPPED'
                warnings.append(f'{rid}: rolled back frontend leaf design: {exc}')
                self.log.warning(warnings[-1], node_id=rid)
        # Process: Publish frontend IR, page evidence, and the design report ===================
        self.save(ir, page_states)
        write_json_atomic(self.root / 'report.json', {
            'status': 'GENERATED_WITH_WARNINGS' if warnings else 'GENERATED',
            'node_states': states, 'leaf_order': order, 'warnings': warnings, 'errors': [],
            'model_attempt': self.attempts, 'model_calls': self.sequence})
        return FrontendDesignResult(ir, page_states, states, warnings=warnings)

    @staticmethod
    def requirement(node, nodes):
        result = {key: copy.deepcopy(node[key]) for key in ('id', 'name', 'description', 'scenarios', 'pages') if key in node}
        for page in result.get('pages', []):
            if page.get('reference'):
                page['reference'] = page['reference'].replace('\\', '/')
        result['ancestor_constraints'] = [
            {key: nodes[parent].get(key, '') for key in ('id', 'name', 'description')}
            for parent in ancestor_ids(nodes, node.get('parent_id'))]
        return result

    def types(self, ir, apis, data, handlers=()):
        roots = [row['schema'] for row in data]
        roots.extend(row[key] for row in handlers for key in ('input', 'output'))
        roots.extend(row['schema'] for row in ir['types'] if row['name'] not in self.backend_type_names)
        wanted = select_types(ir, apis, roots)
        wanted.update(row['name'] for row in ir['types'] if row['name'] not in self.backend_type_names)
        return [copy.deepcopy(row) for row in ir['types'] if row['name'] in wanted]

    def design(self, phase, rid, base, payload, backend):
        apply = {'ui': apply_ui, 'data': apply_data, 'interaction': apply_interaction}[phase]
        def accept(output):
            if phase == 'interaction':
                fresh = expand_api_context(backend, payload, output)
                if fresh:
                    payload['existing_types'] = self.types(base, payload['api_contracts'],
                                                          payload['existing_data'], payload['reuse_handlers'])
                    raise ValueError(f'Loaded existing APIs {fresh} from api_index into api_contracts with their types. '
                                     'Review them before reporting a missing capability or returning the corrected delta.')
            candidate = apply(base, output, rid, payload)
            errors = validate_ir(candidate, backend)
            if errors:
                raise ValueError('; '.join(errors))
            return candidate
        return self.invoke(phase, rid, payload, accept)

    def invoke(self, phase, rid, payload, accept, image_data_url=None):
        if phase == 'visual':
            analyzer = self.legacy_visual()
            before = analyzer.sequence
            try:
                return analyzer.invoke(rid, payload, accept, image_data_url)
            finally:
                self.sequence += analyzer.sequence - before
        return DesignDecision(attempts=self.attempts, schema=SCHEMAS[phase], model=self.model,
            metadata=frontend_metadata(phase), instructions=PROMPTS[phase], payload=payload,
            accept=accept, logger=self.log, requirement_id=rid, label=phase,
            on_attempt=self.count_call,
            failure_prefix=f'{phase}: exhausted {self.attempts} attempts: ').run()

    def count_call(self):
        self.sequence += 1

    def analyze(self, name, reference, rid):
        if self.visual_result is not None:
            normalized = reference.replace('\\', '/')
            if normalized in self.visual_failures:
                raise ValueError(self.visual_failures[normalized])
            if normalized not in self.visual_cache:
                raise ValueError(f'Visual evidence unavailable: {reference}')
            return copy.deepcopy(self.visual_cache[normalized])
        analyzer = self.legacy_visual()
        before = analyzer.sequence
        try:
            return analyzer.analyze(name, reference, rid)
        finally:
            self.sequence += analyzer.sequence - before

    def legacy_visual(self):
        if self._visual_analyzer is None:
            from compiler.visual_analysis import VisualAnalyzer
            self._visual_analyzer = VisualAnalyzer(self.artifact_root, self.reference_root, self.visual_model)
            self._visual_analyzer.cache = self.visual_cache
        return self._visual_analyzer

    def save(self, ir, page_states):
        write_json_atomic(self.root / 'frontend_ir.json', ir)
        write_json_atomic(self.root / 'page_states.json', page_states)

    def page_analysis(self, state):
        references = state.get('references', []) if state else []
        return self.visual_cache.get(references[0]['reference'], {}).get('analysis') if references else None
