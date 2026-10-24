"""Stage execution mechanics without business recovery or persistence policy."""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar


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
    def __init__(self, records: list[StageRecord] | None = None) -> None:
        self.records = records if records is not None else []

    async def run(self, stage: Stage[Payload]) -> StageResult[Payload]:
        if stage._started:
            raise RuntimeError(f'Stage already started: {stage.name}')
        stage._started = True
        record = StageRecord(stage.name)
        self.records.append(record)
        started = time.monotonic()
        try:
            result = await stage._execute()
            if not isinstance(result, StageResult):
                raise TypeError('Stage execution must return StageResult')
            record.status = result.status
            record.diagnostics = result.diagnostics
            return result
        except asyncio.CancelledError:
            record.status = StageStatus.CANCELLED
            raise
        except BaseException as exc:
            record.status = StageStatus.FAILED
            record.diagnostics = (str(exc),)
            raise
        finally:
            record.duration_ms = int((time.monotonic() - started) * 1000)


class Stage(ABC, Generic[Payload]):
    def __init__(self, name: str, *, runner: StageRunner | None = None) -> None:
        self.name = name
        self._runner = runner if runner is not None else StageRunner()
        self._started = False

    async def run(self) -> StageResult[Payload]:
        return await self._runner.run(self)

    @abstractmethod
    async def _execute(self) -> StageResult[Payload]: ...


class BackgroundStage(Stage[Payload]):
    """Own one synchronous worker; cancellation waits for in-flight work to settle."""

    async def _execute(self) -> StageResult[Payload]:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='arc-stage')
        future = asyncio.get_running_loop().run_in_executor(executor, copy_context().run, self._work)
        try:
            return await asyncio.shield(future)
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
