"""Adapt stage lifecycle to the existing public report without business recovery."""
from __future__ import annotations

import asyncio
import inspect

from utils.logging import SynchronousLog

from .contracts import CompilationResult
from .preprocessing.queries import leaf_ids
from .run_report import CompilationRunReport
from .stages import StageStatus


class CompilationProgress:
    def __init__(self, state, parsed, log_cb=None):
        self.state = state
        self.requirements = parsed.requirement_ir
        self.leaves = leaf_ids(self.requirements)
        self.log_cb = log_cb
        self.log = SynchronousLog('Compiler', workspace_root=parsed.output_root)
        state.states.update(parsed.states)
        state.artifacts.update(parsed.artifacts)
        path = parsed.output_root / '.arc/workflow/report.json'
        state.artifacts['workflow'] = str(path)
        self.report = CompilationRunReport(path, state.run_id, state.states, state.artifacts)
        state.report = self.report
        self.records = {}
        self.fatal_exception = None

    async def message(self, message, status=None):
        if self.log_cb is None:
            self.log.write(message, status=status)
        else:
            result = self.log_cb('Compiler', message, status, None)
            if inspect.isawaitable(result):
                await result

    async def started(self, stage, record):
        if stage.reported:
            self.records[id(record)] = self.report.begin(stage.name)
            await self.message(stage.name + ': RUNNING')

    async def finished(self, stage, record, result, failure):
        if not stage.reported:
            if result is not None and stage.warning_target:
                target = next(row for row in reversed(self.report.stages)
                              if row['stage'] == stage.warning_target)
                for warning in result.warnings:
                    self.report.add_warning(target, warning)
                    await self.message(warning, 'warning')
            return
        public = self.records.pop(id(record), None)
        if public is None:
            return
        if failure is not None:
            if isinstance(failure, asyncio.CancelledError):
                public.update(status='CANCELLED', errors=['Cancelled'])
                self.report.save('CANCELLED')
                return
            self.fatal_exception = failure
            message = stage.name + ': ' + str(failure)
            self.report.errors.append(message)
            public.update(status='FAILED', errors=[str(failure)])
            self.report.save('RUNNING')
            await self.message(message, 'warning')
            return
        else:
            errors = result.errors
            path = result.artifact
        if record.status == StageStatus.SKIPPED:
            public.update(status='SKIPPED', warnings=list(result.warnings or result.diagnostics))
            self.report.save('RUNNING')
            return
        if errors or record.status == StageStatus.FAILED:
            for error in errors or record.diagnostics or ('Stage failed',):
                message = stage.name + ': ' + error
                self.report.fail(public, message, path)
                await self.message(message + '; continuing subsequent stages.', 'warning')
            return
        warnings = result.warnings
        status = stage.warnings_status if warnings else 'COMPLETED'
        self.report.complete(public, path, warnings, status=status)
        await self.message(stage.name + ': COMPLETED')
        for warning in warnings:
            await self.message(warning, 'warning')

    def finish(self, *, stopped=False):
        status = 'COMPLETED_WITH_ERRORS' if self.report.errors else 'LOWERED' if stopped else 'COMPLETED'
        self.report.save(status)
        return self.result(completed=True)

    def fail(self, exc):
        if self.fatal_exception is not exc:
            self.report.record_fatal_error(exc)
        if not self.report.failed_nodes:
            self.report.failed_nodes.extend(rid for rid in self.leaves
                if self.state.states[rid] not in {'IMPLEMENTED', 'ALREADY_PASSED'})
        self.report.save('FAILED')
        return self.result(completed=False)

    def cancel(self):
        self.report.save('CANCELLED')

    def result(self, *, completed):
        return CompilationResult(ok=not self.report.errors and completed,
            root_id=self.requirements['root_id'], states=self.state.states,
            failed_nodes=self.report.failed_nodes, artifacts=self.state.artifacts,
            run_id=self.state.run_id, completed=completed)
