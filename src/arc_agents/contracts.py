from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProposedEdit:
    """Replace one uniquely identified source fragment inside a module region."""

    module_id: str
    expected_sha256: str
    search: str
    replacement: str


@dataclass(frozen=True, slots=True)
class ProposedPatch:
    requirement_id: str
    edits: tuple[ProposedEdit, ...]
