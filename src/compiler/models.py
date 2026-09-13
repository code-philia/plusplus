from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A stable compiler diagnostic emitted by a deterministic pass."""

    code: str
    message: str
    severity: Severity = "error"
    node_id: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "node_id": self.node_id,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class CompilationRequest:
    """Everything callers must provide to run the compiler."""

    requirement_path: Path
    output_dir: Path
    app_type: str = "web"
    web_port: int = 3301
    resume: bool = False
    retry_failed: bool = False
    retry_node_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class CompilationResult:
    """Compiler outcome independent of CLI rendering."""

    ok: bool
    complete: bool
    root_id: str | None = None
    states: dict[str, str] = field(default_factory=dict)
    failed_nodes: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok and self.complete,
            "frontend_ok": self.ok,
            "complete": self.complete,
            "root_id": self.root_id,
            "states": dict(sorted(self.states.items())),
            "failed_nodes": sorted(self.failed_nodes),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "artifacts": dict(sorted(self.artifacts.items())),
        }
