from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .context import RuntimePaths
from .events import EventClient
from .traceability import TraceabilityStore


@dataclass(frozen=True, slots=True)
class AgentRuntime:
    paths: RuntimePaths
    events: EventClient
    traceability: TraceabilityStore

    @classmethod
    def for_project(cls, project_dir: str | Path) -> "AgentRuntime":
        paths = RuntimePaths.for_project(project_dir)
        paths.ensure_parent_dirs()
        events = EventClient(paths)
        return cls(
            paths=paths,
            events=events,
            traceability=TraceabilityStore(paths, events),
        )
