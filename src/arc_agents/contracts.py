from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProposedEdit:
    """Replace only the source text between one module's implementation markers."""

    module_id: str
    expected_sha256: str
    replacement: str


@dataclass(frozen=True, slots=True)
class ProposedPatch:
    requirement_id: str
    edits: tuple[ProposedEdit, ...]
