from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from compiler.runtime.paths import RuntimePaths
from compiler.runtime.events import EventClient
from compiler.runtime.traceability import TraceabilityStore


@dataclass(frozen=True, slots=True)
class CompilationContext:
    paths: RuntimePaths
    events: EventClient
    traceability: TraceabilityStore

    @classmethod
    def for_project(
        cls,
        project_dir: str | Path,
        *,
        run_id: str | None = None,
    ) -> "CompilationContext":
        paths = RuntimePaths.for_project(project_dir)
        paths.ensure_parent_dirs()
        events = EventClient(paths, run_id=run_id)
        return cls(
            paths=paths,
            events=events,
            traceability=TraceabilityStore(paths, events),
        )
