"""File selection shared by model consumers; reasons stay in compiler logs."""
from __future__ import annotations

def file_selection_log(requirement_id: str, phase: str, files: dict[str, str], reason: str) -> str:
    return (f"FILE_CONTEXT requirement={requirement_id} phase={phase} reason={reason} "
            f"files={sorted(files)} chars={sum(len(source) for source in files.values())}")
