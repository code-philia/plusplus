from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProposedEdit:
    """Replace one exact source fragment in a writable source file."""

    file: str
    expected_sha256: str
    search: str
    replacement: str


@dataclass(frozen=True, slots=True)
class ProposedPatch:
    requirement_id: str
    edits: tuple[ProposedEdit, ...]
