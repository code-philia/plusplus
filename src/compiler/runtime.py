"""Per-compilation ownership and process-local workspace exclusion."""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterator, Protocol

from .contracts import CompilationRequest, CompilationResult
from .budgets import ExecutionBudgets
from .run_report import CompilationRunReport
from .stages import StageRecord


class CompilationPipeline(Protocol):
    async def run(self) -> CompilationResult: ...


@dataclass(slots=True)
class CompilationState:
    status: str = 'CREATED'
    run_id: str | None = None
    output_root: Path | None = None
    states: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    stages: list[StageRecord] = field(default_factory=list)
    report: CompilationRunReport | None = None
    result: CompilationResult | None = None
    budgets: ExecutionBudgets | None = None


@dataclass(frozen=True, slots=True)
class CompilerDependencies:
    model: Any = None
    log_cb: Callable[..., Any] | None = None
    pipeline_factory: Callable[
        [CompilationRequest, CompilationState, CompilerDependencies], CompilationPipeline
    ] | None = None

    def load_budgets(self) -> ExecutionBudgets:
        from utils.environment import ensure_dotenv_loaded
        ensure_dotenv_loaded()
        return ExecutionBudgets.from_environment()

    def model_client(self):
        if self.model is not None:
            return self.model
        from model.client import Model
        return Model.from_env()


class WorkspaceLeases:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active: set[str] = set()

    @contextmanager
    def claim(self, root: Path) -> Iterator[None]:
        key = os.path.normcase(str(root.resolve()))
        with self._lock:
            if key in self._active:
                raise RuntimeError(f'Workspace already has an active compilation: {root}')
            self._active.add(key)
        try:
            yield
        finally:
            with self._lock:
                self._active.remove(key)


workspace_leases = WorkspaceLeases()
