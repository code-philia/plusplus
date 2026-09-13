from __future__ import annotations

from pathlib import Path
from typing import Any

from arcbench_agent_runtime.jsonio import write_json_atomic

from .models import Diagnostic


class CompilerArtifactStore:
    """Persist versioned compiler artifacts behind one filesystem interface."""

    def __init__(self, output_dir: Path) -> None:
        self.root = output_dir.expanduser().resolve() / ".arc" / "compiler"

    def write_frontend(
        self,
        *,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
        diagnostics: list[Diagnostic],
    ) -> dict[str, str]:
        self.root.mkdir(parents=True, exist_ok=True)
        paths = {
            "requirement_ir": self.root / "requirement_ir.json",
            "dependency_graph": self.root / "dependency_graph.json",
            "diagnostics": self.root / "diagnostics.json",
        }
        write_json_atomic(paths["requirement_ir"], requirement_ir)
        write_json_atomic(paths["dependency_graph"], dependency_graph)
        write_json_atomic(
            paths["diagnostics"],
            {
                "schema_version": 1,
                "items": [item.to_dict() for item in diagnostics],
            },
        )
        return {name: str(path) for name, path in paths.items()}

    def write_queue(self, *, root_id: str | None, node_states: dict[str, str], frontend_ok: bool) -> str:
        path = self.root.parent / "processing_queue.json"
        passes = [
            ("FRONTEND", "COMPLETED" if frontend_ok else "FAILED"),
            ("DISCOVERY", "PENDING"),
            ("DESIGN", "PENDING"),
            ("LOWERING", "PENDING"),
            ("IMPLEMENTATION", "PENDING"),
            ("ACCEPTANCE", "PENDING"),
        ]
        write_json_atomic(
            path,
            {
                "schema_version": 2,
                "root_id": root_id,
                "passes": [
                    {"pass_id": pass_id, "order": index, "status": status}
                    for index, (pass_id, status) in enumerate(passes)
                ],
                "node_states": dict(sorted(node_states.items())),
                "last_pass_id": "FRONTEND",
            },
        )
        return str(path)
