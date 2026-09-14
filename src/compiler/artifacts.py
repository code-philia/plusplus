from __future__ import annotations

from pathlib import Path
from typing import Any

from arcbench_agent_runtime.jsonio import read_json, write_json_atomic

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

    def read_database(self) -> tuple[dict[str, Any] | None, str | None]:
        """Read the existing database artifact without modifying it."""

        path = self.root / "database_schema.json"
        if not path.is_file():
            return None, f"Database schema artifact does not exist: {path}"
        schema = read_json(path, None)
        if not isinstance(schema, dict):
            return None, f"Database schema artifact must contain a JSON object: {path}"
        return schema, None

    def write_design(
        self,
        *,
        design_ir: dict[str, Any],
        design_sha256: str | None,
        database_sha256: str | None,
    ) -> dict[str, str]:
        paths = {"design_ir": self.root / "design_ir.json"}
        write_json_atomic(paths["design_ir"], design_ir)
        if design_sha256:
            paths["design_manifest"] = self.root / "design_manifest.json"
            write_json_atomic(
                paths["design_manifest"],
                {
                    "schema_version": 1,
                    "design_sha256": design_sha256,
                    "database_sha256": database_sha256,
                },
            )
        return {name: str(path) for name, path in paths.items()}

    def write_pass_queue(
        self,
        *,
        root_id: str | None,
        node_states: dict[str, str],
        frontend_ok: bool,
        database_status: str,
        design_status: str = "PENDING",
    ) -> str:
        path = self.root.parent / "processing_queue.json"
        passes = [
            ("FRONTEND", "COMPLETED" if frontend_ok else "FAILED"),
            ("DATABASE_SCHEMA", database_status),
            ("DESIGN", design_status),
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
                "last_pass_id": (
                    "DESIGN"
                    if design_status != "PENDING"
                    else "DATABASE_SCHEMA"
                    if database_status != "PENDING"
                    else "FRONTEND"
                ),
            },
        )
        return str(path)
