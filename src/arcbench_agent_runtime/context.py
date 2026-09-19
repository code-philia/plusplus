from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    project_dir: Path
    runner_events_path: Path
    traceability_dir: Path

    @classmethod
    def for_project(cls, project_dir: str | Path) -> "RuntimePaths":
        root = Path(project_dir).expanduser().resolve()
        return cls(
            project_dir=root,
            runner_events_path=root / ".arc" / "runner-events.jsonl",
            traceability_dir=root / ".arc" / "traceability",
        )

    def ensure_parent_dirs(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.runner_events_path.parent.mkdir(parents=True, exist_ok=True)
        self.traceability_dir.mkdir(parents=True, exist_ok=True)
