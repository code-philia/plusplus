"""Optional compilation workspace archives."""
from __future__ import annotations

from pathlib import Path
from model.usage import enabled
from utils.archive import snapshot_workspace


def stage_checkpoint(root: Path, label: str) -> None:
    if enabled():
        snapshot_workspace(root, label)


