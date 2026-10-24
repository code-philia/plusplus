"""Explicit, scoped model-call presentation and logging metadata."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True, slots=True)
class ContextGroup:
    name: str
    purpose: str
    keys: frozenset[str]


@dataclass(frozen=True, slots=True)
class ContextLayout:
    groups: tuple[ContextGroup, ...] = ()
    task: str | None = None
    remaining_name: str = 'Additional context'
    remaining_purpose: str = ''


@dataclass(frozen=True, slots=True)
class CallMetadata:
    operation: str
    layout: ContextLayout
    log_enabled: bool = True
    log_scope: str | None = None


_metadata: ContextVar[CallMetadata | None] = ContextVar('model_call_metadata', default=None)


def current_call() -> CallMetadata | None:
    return _metadata.get()


@contextmanager
def call_scope(metadata: CallMetadata) -> Iterator[None]:
    token = _metadata.set(metadata)
    try:
        yield
    finally:
        _metadata.reset(token)
