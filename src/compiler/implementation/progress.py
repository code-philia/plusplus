"""Durable requirement progress for compilation-driven implementation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.jsonio import write_json_atomic



class CompilationProgress:
    def __init__(self, root: Path, requirement_ir: dict[str, Any], order: list[str], *, resume: bool) -> None:
        self.path = root / ".arc/compile_only/progress.json"
        fingerprint = hashlib.sha256(json.dumps(requirement_ir, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.data: dict[str, Any] = {"requirement_fingerprint": fingerprint, "order": order, "nodes": {}}
        if resume and self.path.is_file():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("Compilation progress must be an object")
            if loaded.get("requirement_fingerprint") != fingerprint or loaded.get("order") != order:
                raise ValueError("Compilation progress does not match the requirement IR or traversal order")
            nodes = loaded.get("nodes")
            if not isinstance(nodes, dict) or set(nodes) - set(order):
                raise ValueError("Invalid Compilation progress nodes")
            if any(not isinstance(row, dict) or row.get("status") not in
                   {"STARTED", "FAILED", "COMPILED"} for row in nodes.values()):
                raise ValueError("Invalid Compilation progress status")
            self.data = loaded
        self.save()

    @property
    def nodes(self) -> dict[str, Any]:
        return self.data["nodes"]

    def mark(self, rid: str, status: str, stage: str) -> None:
        self.nodes[rid] = {"status": status, "stage": stage,
                           "updated_at": datetime.now(timezone.utc).isoformat()}
        self.save()

    def save(self) -> None:
        write_json_atomic(self.path, self.data)
