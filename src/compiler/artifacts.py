from __future__ import annotations

from pathlib import Path
from typing import Any

from arcbench_agent_runtime.jsonio import write_json_atomic

class CompilerArtifactStore:
    """Persist versioned compiler artifacts behind one filesystem interface."""

    def __init__(self, output_dir: Path) -> None:
        self.root = output_dir.expanduser().resolve() / ".arc" / "compiler"

    def write_frontend(
        self,
        *,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
    ) -> dict[str, str]:
        self.root.mkdir(parents=True, exist_ok=True)
        paths = {
            "requirement_ir": self.root / "requirement_ir.json",
            "dependency_graph": self.root / "dependency_graph.json",
        }
        write_json_atomic(paths["requirement_ir"], requirement_ir)
        write_json_atomic(paths["dependency_graph"], dependency_graph)
        return {name: str(path) for name, path in paths.items()}

    def write_queue(self, *, root_id: str | None, node_states: dict[str, str], frontend_ok: bool) -> str:
        path = self.root.parent / "processing_queue.json"
        passes = [
            ("FRONTEND", "COMPLETED" if frontend_ok else "FAILED"),
            ("DATABASE_SCHEMA", "PENDING"),
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

    def write_database(
        self,
        *,
        schema: dict[str, Any],
    ) -> dict[str, str]:
        paths = {"database_schema": self.root / "database_schema.json"}
        write_json_atomic(paths["database_schema"], schema)
        return {name: str(path) for name, path in paths.items()}

    def write_pass_queue(
        self,
        *,
        root_id: str | None,
        node_states: dict[str, str],
        frontend_ok: bool,
        database_status: str,
    ) -> str:
        path = self.root.parent / "processing_queue.json"
        passes = [
            ("FRONTEND", "COMPLETED" if frontend_ok else "FAILED"),
            ("DATABASE_SCHEMA", database_status),
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
                "last_pass_id": "DATABASE_SCHEMA" if database_status != "PENDING" else "FRONTEND",
            },
        )
        return str(path)
