"""Stage snapshots committed with source history; restart on an isolated worktree."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from arcbench_agent_runtime.jsonio import write_json_atomic
from .git_history import GitStageError, ProjectGitHistory
from .process_utils import resolve_executable, run_command


START_FROM = ("zero", "initialized", "database", "backend-ir", "frontend-ir", "lowered")
SNAPSHOT = ".arc/checkpoints/current.json"
REQUIRED = {
    "initialized": set(),
    "database": {"preprocessing", "database"},
    "backend-ir": {"preprocessing", "database", "database_manifest", "design"},
    "frontend-ir": {"preprocessing", "database", "database_manifest", "design", "backend_routes", "frontend"},
    "lowered": {"preprocessing", "database", "database_manifest", "design", "backend_routes", "frontend"},
}


class CheckpointStore:
    def __init__(self, root: Path, requirement_path: Path, port: int) -> None:
        self.root = root
        self.requirement_path = requirement_path
        self.port = port
        self.payload: dict[str, Any] = {}

    def save(self, stage: str, **values: Any) -> None:
        self.payload.update(values)
        write_json_atomic(self.root / SNAPSHOT, {
            "stage": stage,
            "requirement_sha256": hashlib.sha256(self.requirement_path.read_bytes()).hexdigest(),
            "web_port": self.port,
            "payload": self.payload,
        })
        ProjectGitHistory(self.root).commit(f"checkpoint {stage}", [SNAPSHOT])

    def load(self, stage: str, *, resume: bool = False) -> dict[str, Any]:
        try:
            snapshot = json.loads((self.root / SNAPSHOT).read_text(encoding="utf-8"))
            validate_snapshot(snapshot, stage)
            if stage != "initialized" and snapshot["requirement_sha256"] != hashlib.sha256(self.requirement_path.read_bytes()).hexdigest():
                raise ValueError("Requirement document differs from checkpoint; start from zero/initialized with the intended source.")
            if snapshot["web_port"] != self.port:
                raise ValueError(f"Checkpoint uses port {snapshot['web_port']}; use the same --port.")
            history = ProjectGitHistory(self.root)
            committed = json.loads(history._run(["show", f"HEAD:{SNAPSHOT}"]))
            if committed != snapshot:
                raise ValueError("Checkpoint snapshot differs from HEAD; select a committed checkpoint.")
            protected = ([".arc/design", ".arc/preprocessing"] if resume else
                         ["backend", "frontend", "shared", "package.json", "package-lock.json", ".arc/design",
                          ".arc/lowering", ".arc/preprocessing", ".arc/project"])
            changed = history._run(["diff", "--name-only", "HEAD", "--", *protected])
            if changed:
                raise ValueError("Checkpoint source has uncommitted changes: " + changed)
            self.payload = snapshot["payload"]
            return self.payload
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GitStageError(f"Cannot start from {stage}: {exc}") from exc


def validate_snapshot(snapshot: Any, stage: str) -> None:
    if not isinstance(snapshot, dict) or snapshot.get("stage") != stage:
        raise ValueError(f"Selected Git commit is not a {stage} checkpoint")
    payload = snapshot.get("payload")
    if not isinstance(payload, dict) or REQUIRED[stage] - payload.keys():
        raise ValueError(f"Incomplete {stage} checkpoint payload")
    if any(not isinstance(payload[name], dict) for name in REQUIRED[stage]):
        raise ValueError("Checkpoint payload sections must be objects")
    if stage != "initialized":
        preprocessing = payload["preprocessing"]
        if not all(isinstance(preprocessing.get(k), dict) for k in ("requirement_ir", "dependency_graph")):
            raise ValueError("Checkpoint has no complete preprocessing artifacts")
        if not preprocessing["requirement_ir"].get("root_id"):
            raise ValueError("Checkpoint Requirement IR has no root")
    if stage in {"backend-ir", "frontend-ir", "lowered"} and payload["design"].get("ir_kind") != "backend_design":
        raise ValueError("Checkpoint requires the current Backend IR; regenerate backend design.")
    if stage in {"frontend-ir", "lowered"} and payload["frontend"]["report"]["status"] not in {"GENERATED", "GENERATED_WITH_WARNINGS"}:
        raise ValueError("Partial frontend IR cannot be used as a checkpoint")
    if stage in {"frontend-ir", "lowered"}:
        frontend = payload["frontend"].get("frontend_ir")
        if not isinstance(frontend, dict) or (frontend.get("ir_kind") != "frontend_design" or not frontend.get("root_ui_id")):
            raise ValueError("Checkpoint has no frontend UI root")


def prepare_restart(source: Path, stage: str, revision: str | None = None,
                    destination: Path | None = None, requirement_path: Path | None = None,
                    requested_port: int | None = None) -> tuple[Path, int, str]:
    """Use only committed state. Dirty source and its active branch are never touched."""
    if stage not in REQUIRED:
        raise GitStageError(f"Unknown restart stage {stage}")
    source = source.resolve()
    if not (source / ".git").exists():
        raise GitStageError(f"Not a generated Git project: {source}")
    history = ProjectGitHistory(source)
    if revision:
        commit = history._run(["rev-parse", "--verify", "--end-of-options", revision + "^{commit}"])
    else:
        commit = history._run(["log", "-1", "--format=%H", "--fixed-strings",
                               "--grep", f"ARC: checkpoint {stage}", "HEAD"])
    if not commit:
        raise GitStageError(f"No completed {stage} checkpoint found in HEAD history")
    try:
        snapshot = json.loads(history._run(["show", f"{commit}:{SNAPSHOT}"]))
        validate_snapshot(snapshot, stage)
        if requested_port is not None and requested_port != int(snapshot["web_port"]):
            raise ValueError(f"Checkpoint requires --port {snapshot['web_port']}")
        if requirement_path is not None and stage != "initialized":
            if snapshot["requirement_sha256"] != hashlib.sha256(requirement_path.read_bytes()).hexdigest():
                raise ValueError("Requirement document differs from the selected checkpoint")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GitStageError(f"Invalid checkpoint at {commit}: {exc}") from exc
    suffix = uuid.uuid4().hex[:8]
    target = destination.resolve() if destination else source.with_name(f"{source.name}-from-{stage}-{suffix}")
    if target.exists() or target == source or source in target.parents:
        raise GitStageError("Restart output must be a new directory outside the source project")
    history._run(["worktree", "add", "-b", f"arc/restart/{stage}/{suffix}", str(target), commit])
    return target, int(snapshot["web_port"]), commit


def install_restart_dependencies(root: Path) -> None:
    """Ignored node_modules are not in Git; recreate links in the new worktree."""
    executable = resolve_executable("npm", os.environ)
    if executable is None:
        raise GitStageError("npm is required in the restart worktree")
    try:
        result = run_command([executable, "ci"], cwd=root, environment=os.environ, timeout=900)
    except Exception as exc:
        raise GitStageError(f"Restart dependency installation failed: {exc}") from exc
    if result.returncode:
        raise GitStageError(f"Restart npm ci failed: {result.stderr or result.stdout}")
