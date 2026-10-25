"""Single-writer image evidence and cooperative background lifetime."""
from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compiler.context.model_calls import invoke_model
from compiler.frontend_design.presentation import FRONTEND
from model.call_context import CallMetadata

from compiler.frontend_design.contracts import PROMPTS, SCHEMAS, VISUAL_FIELDS, VISUAL_ANALYSIS_VERSION
from compiler.frontend_design.visual import image_evidence
from compiler.context.structured import DesignDecision
from compiler.preprocessing.queries import unit_ids
from compiler.budgets import positive_limit
from compiler.stages import BackgroundStage, StageResult, StageStatus
from utils.jsonio import read_json, write_json_atomic
from utils.logging import SynchronousLog


class VisualAnalyzer:
    def __init__(self, artifact_root, reference_root, model=None, *, cancel_requested=None):
        self.root, self.reference_root = Path(artifact_root), reference_root
        self.path = self.root / 'visual_cache.json'
        self.cache = read_json(self.path, {})
        self.model = model
        self.log = SynchronousLog('VisualAnalysis', workspace_root=self.root.parent)
        self.attempts = positive_limit('MODEL_ATTEMPT', 5)
        self.sequence = 0
        self.cancel_requested = cancel_requested

    def invoke(self, rid, payload, accept, image):
        def before_attempt():
            if self.cancel_requested is not None and self.cancel_requested.is_set():
                raise asyncio.CancelledError
            self.sequence += 1

        def invoke(request):
            if self.model is None:
                from model.vision import VisualModel
                self.model = VisualModel.from_env()
            if hasattr(self.model, 'set_usage_path'):
                self.model.set_usage_path(self.root / 'model_usage.jsonl')
            return invoke_model(self.model, CallMetadata('frontend_design_visual', FRONTEND, log_scope='visual_analysis'), method='generate_visual_json',
                schema_name='frontend_design_visual', instructions=PROMPTS['visual'], input_payload=request,
                output_schema=SCHEMAS['visual'], image_data_url=image)

        return DesignDecision(attempts=self.attempts, schema=SCHEMAS['visual'], model=self.model,
            metadata=CallMetadata('frontend_design_visual', FRONTEND, log_scope='visual_analysis'), instructions=PROMPTS['visual'], payload=payload,
            accept=accept, logger=self.log, requirement_id=rid, label='visual',
            on_attempt=before_attempt, failure_prefix=f'visual: exhausted {self.attempts} attempts: ',
            invoke=invoke).run()

    def analyze(self, name, reference, rid):
        digest, image = image_evidence(reference, self.reference_root)
        cached = self.cache.get(reference)
        if (isinstance(cached, dict) and cached.get('sha256') == digest
                and cached.get('analysis_version') == VISUAL_ANALYSIS_VERSION
                and isinstance(cached.get('analysis'), str) and cached['analysis'].strip()):
            self.log.info(f'Cache hit: {name} ({reference})', node_id=rid)
            return copy.deepcopy(cached)
        self.log.info(f'Analyzing: {name} ({reference})', node_id=rid)
        def accept(output):
            for field in VISUAL_FIELDS:
                if not output[field] or any(not item.strip() for item in output[field]):
                    raise ValueError(f'Visual analysis must include nonempty {field}')
            return output
        output = self.invoke(rid, {'current_task': {'page': {'name': name, 'reference': reference}}}, accept, image)
        analysis = '\n\n'.join(field + ':\n' + '\n'.join('- ' + item for item in output[field])
                               for field in VISUAL_FIELDS)
        record = {'reference': reference, 'sha256': digest, 'analysis_version': VISUAL_ANALYSIS_VERSION,
                  'analysis': analysis}
        self.cache[reference] = record
        write_json_atomic(self.path, self.cache)
        from utils.git import checkpoint
        checkpoint(self.root.parent, 'visual_analysis', node=rid, phase='reference',
                   paths=[self.path.relative_to(self.root.parent).as_posix()], payload=record, status='PUBLISHED')
        self.log.info(f'Analysis completed: {name} ({reference})', node_id=rid)
        return copy.deepcopy(record)


@dataclass(frozen=True, slots=True)
class VisualAnalysisInput:
    requirements: dict[str, Any]
    artifact_root: Path
    reference_root: Path


@dataclass(frozen=True, slots=True)
class VisualAnalysisOutput:
    cache: dict[str, Any]
    failures: dict[str, str]
    model_calls: int = 0


class VisualAnalysisStage(BackgroundStage[VisualAnalysisOutput]):
    warnings_status = 'COMPLETED_WITH_WARNINGS'

    def __init__(self, inputs: VisualAnalysisInput, *, model=None, runner=None):
        super().__init__('visual_analysis', runner=runner)
        self.inputs, self.model = inputs, model
        self.stop = self.cancel_requested
        self.task = None

    def _work(self):
        try:
            analyzer = VisualAnalyzer(self.inputs.artifact_root, self.inputs.reference_root, self.model,
                                      cancel_requested=self.stop)
        except Exception as exc:
            return StageResult(StageStatus.DEGRADED,
                VisualAnalysisOutput({}, {'background_analysis': str(exc)}),
                warnings=('background_analysis: ' + str(exc),), artifact=self.inputs.artifact_root / 'visual_cache.json')
        failures, seen = {}, set()
        for rid in unit_ids(self.inputs.requirements):
            for page in self.inputs.requirements['nodes'][rid].get('pages', []):
                if self.stop.is_set():
                    break
                reference = (page.get('reference') or '').replace('\\', '/')
                if reference and reference not in seen:
                    seen.add(reference)
                    try:
                        analyzer.analyze(page.get('name') or page.get('image_id'), reference, rid)
                    except Exception as exc:
                        failures[reference] = str(exc)
                        analyzer.log.warning(f'Skipping {reference}: {exc}', node_id=rid)
        warnings = tuple(f'{path}: {message}' for path, message in failures.items())
        return StageResult(StageStatus.DEGRADED if failures else StageStatus.SUCCEEDED,
            VisualAnalysisOutput(analyzer.cache, failures, analyzer.sequence), warnings=warnings,
            artifact=self.inputs.artifact_root / 'visual_cache.json')

    async def join(self):
        if self.task is None:
            raise RuntimeError('Visual background task has not started')
        return await self.task

    @asynccontextmanager
    async def background(self):
        if self.task is not None:
            raise RuntimeError('Visual background task already started')
        self.task = asyncio.create_task(self.run())
        ready = asyncio.create_task(self.ready.wait())
        try:
            await asyncio.wait({self.task, ready}, return_when=asyncio.FIRST_COMPLETED)
            if self.task.done():
                self.task.result()
            yield self
        finally:
            ready.cancel()
            if not self.task.done():
                self.stop.set()
                self.task.cancel()
            while not self.task.done():
                try:
                    await asyncio.shield(self.task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not self.task.cancelled():
                self.task.exception()
            await asyncio.gather(ready, return_exceptions=True)
