"""Stage lifecycle and workspace checkpoint reporting without business recovery policy."""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Generic, TypeVar
from utils.git import checkpoint


Payload = TypeVar('Payload')


class StageStatus(str, Enum):
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    DEGRADED = 'DEGRADED'
    FAILED = 'FAILED'
    SKIPPED = 'SKIPPED'
    CANCELLED = 'CANCELLED'


@dataclass(frozen=True, slots=True)
class StageResult(Generic[Payload]):
    status: StageStatus
    value: Payload | None = None
    diagnostics: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    artifact: Path | None = None

    def require_value(self) -> Payload:
        if self.value is None:
            raise RuntimeError(f'Stage has no usable payload: {self.status.value}')
        return self.value

    def __post_init__(self) -> None:
        if self.status not in {
            StageStatus.SUCCEEDED, StageStatus.DEGRADED, StageStatus.FAILED, StageStatus.SKIPPED,
        }:
            raise ValueError('StageResult must describe a settled, non-cancelled stage')
        if self.status in {StageStatus.SUCCEEDED, StageStatus.DEGRADED} and self.value is None:
            raise ValueError('Successful or degraded stages require a usable payload')
        if self.status in {StageStatus.FAILED, StageStatus.SKIPPED} and self.value is not None:
            raise ValueError('Failed or skipped stages cannot expose a usable payload')


@dataclass(slots=True)
class StageRecord:
    name: str
    status: StageStatus = StageStatus.RUNNING
    diagnostics: tuple[str, ...] = ()
    duration_ms: int = 0


class StageRunner:
    def __init__(self, records: list[StageRecord] | None = None, *, observer=None, checkpoint_root: Path | None = None) -> None:
        self.records = records if records is not None else []
        self.observer = observer
        self.checkpoint_root = checkpoint_root

    async def run(self, stage: Stage[Payload]) -> StageResult[Payload]:
        if stage._started:
            raise RuntimeError(f'Stage already started: {stage.name}')
        stage._started = True
        record = StageRecord(stage.name)
        self.records.append(record)
        started = time.monotonic()
        result = None
        failure = None
        try:
            if self.observer is not None:
                await self.observer.started(stage, record)
            result = await stage._execute()
            if not isinstance(result, StageResult):
                result = None
                raise TypeError('Stage execution must return StageResult')
            record.status = result.status
            record.diagnostics = result.diagnostics + result.errors + result.warnings
            return result
        except asyncio.CancelledError as exc:
            failure = exc
            record.status = StageStatus.CANCELLED
            raise
        except BaseException as exc:
            failure = exc
            record.status = StageStatus.FAILED
            record.diagnostics = (str(exc),)
            raise
        finally:
            record.duration_ms = int((time.monotonic() - started) * 1000)
            if self.observer is not None:
                await self.observer.finished(stage, record, result, failure)
            root = self.checkpoint_root
            artifact = result.artifact.resolve() if result is not None and result.artifact is not None else None
            if root is None and artifact is not None:
                root = next((parent.parent for parent in artifact.parents if parent.name == '.arc'), None)
            if root is not None:
                root = Path(root).resolve()
                paths = [artifact.relative_to(root).as_posix()] if artifact is not None and artifact.is_relative_to(root) else []
                checkpoint(root, 'stage', phase=stage.name, status=record.status.value, paths=paths,
                           payload={'diagnostics': record.diagnostics, 'duration_ms': record.duration_ms})


class Stage(ABC, Generic[Payload]):
    warnings_status = 'COMPLETED'

    def __init__(self, name: str, *, runner: StageRunner | None = None,
                 reported: bool = True, warning_target: str | None = None) -> None:
        self.name = name
        self._runner = runner if runner is not None else StageRunner()
        self._started = False
        self.reported = reported
        self.warning_target = warning_target

    async def run(self) -> StageResult[Payload]:
        return await self._runner.run(self)

    @abstractmethod
    async def _execute(self) -> StageResult[Payload]: ...


class BackgroundStage(Stage[Payload]):
    """Own one synchronous worker; cancellation waits for in-flight work to settle."""

    def __init__(self, name: str, *, runner: StageRunner | None = None) -> None:
        super().__init__(name, runner=runner)
        self.cancel_requested = Event()
        self.ready = asyncio.Event()

    async def _execute(self) -> StageResult[Payload]:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='arc-stage')
        future = asyncio.get_running_loop().run_in_executor(executor, copy_context().run, self._work)
        self.ready.set()
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            self.cancel_requested.set()
            raise
        finally:
            try:
                while not future.done():
                    try:
                        await asyncio.shield(future)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if future.done() and not future.cancelled():
                    future.exception()
            finally:
                executor.shutdown(wait=True, cancel_futures=True)

    @abstractmethod
    def _work(self) -> StageResult[Payload]: ...
