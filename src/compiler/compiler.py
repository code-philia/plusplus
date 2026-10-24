"""Public compiler lifecycle; command parsing and stage algorithms live elsewhere."""
from __future__ import annotations

import asyncio
import uuid
from threading import Lock
from typing import Any

from utils.logging import run_scope

from .contracts import CompilationRequest, CompilationResult
from .runtime import CompilationState, CompilerDependencies, workspace_leases


class Compiler:
    def __init__(self, request: CompilationRequest | None = None,
                 log_cb: Any = None, model: Any = None, *,
                 dependencies: CompilerDependencies | None = None,
                 runtime: Any = None) -> None:
        if request is not None and not isinstance(request, CompilationRequest):
            request = None
        if dependencies is not None and (model is not None or log_cb is not None):
            raise ValueError('Pass dependencies or model/log_cb, not both')
        self.request = request
        self.dependencies = dependencies if dependencies is not None else CompilerDependencies(model, log_cb)
        self.state = CompilationState()
        self._start_lock = Lock()
        if request is not None:
            self._validate(request)

    @staticmethod
    def _validate(request: CompilationRequest) -> None:
        if request.resume or request.start_from != 'zero':
            raise ValueError('Checkpoint resume is not supported by the current IR; use the individual stage scripts.')
        if not 1 <= request.web_port <= 65535:
            raise ValueError('Port must be between 1 and 65535')

    async def compile(self, request: CompilationRequest) -> CompilationResult:
        return await self._compilation(request)

    async def compilation(self) -> CompilationResult:
        if self.request is None:
            raise ValueError('CompilationRequest is required before compilation')
        return await self._compilation(self.request)

    async def _compilation(self, request: CompilationRequest) -> CompilationResult:
        with self._start_lock:
            if self.state.status != 'CREATED':
                raise RuntimeError('A Compiler instance can only run once')
            if self.request is not None and self.request != request:
                raise ValueError('CompilationRequest differs from the constructor request')
            self.request = request
            self.state.status = 'RUNNING'
        try:
            self._validate(request)
            output = request.output_dir.expanduser().resolve()
            self.state.output_root = output
            self.state.run_id = uuid.uuid4().hex
            with workspace_leases.claim(output), run_scope(self.state.run_id):
                factory = self.dependencies.pipeline_factory
                if factory is None:
                    from .pipeline import _BusinessPipeline
                    pipeline = _BusinessPipeline(request, self.state, self.dependencies)
                else:
                    pipeline = factory(request, self.state, self.dependencies)
                result = await pipeline.run()
                self.state.result = result
                self.state.status = 'COMPLETED' if result.completed else 'FAILED'
                return result
        except asyncio.CancelledError:
            self.state.status = 'CANCELLED'
            raise
        except BaseException:
            self.state.status = 'FAILED'
            raise
