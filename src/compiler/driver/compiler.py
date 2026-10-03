from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from ..frontend.pipeline import RequirementFrontend
from ..models import CompilationRequest, CompilationResult


class ARCCompiler:
    """Public compiler driver with a formal frontend boundary.

    The existing implementation passes remain behind the boundary until their
    dictionary protocols are migrated to typed Design and Implementation IR.
    """

    def __init__(self, runtime: Any, log_cb: Any, model: Any | None = None, *, target: str = "react-fastapi") -> None:
        self.runtime = runtime
        self.log_cb = log_cb
        self.model = model
        self.target = target
        self.frontend = RequirementFrontend()
        self._controller: Any | None = None

    async def _log_frontend_errors(self, errors: list[str]) -> None:
        for error in errors:
            if self.log_cb is None:
                continue
            result = self.log_cb("Frontend", error, "error", None)
            if inspect.isawaitable(result):
                await result

    async def compile(self, request: CompilationRequest) -> CompilationResult:
        frontend = self.frontend.compile(request.requirement_path)
        if not frontend.ok:
            await self._log_frontend_errors(frontend.preprocessing.errors)
            return CompilationResult(ok=False, artifacts={})
        if self._controller is None:
            from .controller import CompilerController

            self._controller = CompilerController(self.runtime, self.log_cb, self.model, target=self.target)
        return await self._controller.compile(request)


Compiler = ARCCompiler

__all__ = ["ARCCompiler", "Compiler"]