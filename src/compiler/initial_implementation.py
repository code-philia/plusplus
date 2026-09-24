"""Deterministic target planning and ledger support for first-pass implementation.

The initial implementation pass is intentionally separate from test failure
localization.  Code Binding ownership decides which business modules receive a
first implementation opportunity; frozen tests only constrain the later repair
loops.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any


BACKEND_INITIAL_KINDS = frozenset({"DB", "FUNC", "API"})
FRONTEND_INITIAL_KINDS = frozenset({"STORE", "COMPONENT", "PAGE", "LAYOUT"})
COMPILER_OWNED_KINDS = frozenset({"API_CLIENT", "TYPE", "ROUTE", "GLUE"})


@dataclass(slots=True)
class InitialImplementationEntry:
    module_id: str
    kind: str
    file: str
    status: str = "PENDING"
    attempts: int = 0
    changed_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class InitialImplementationLedger:
    requirement_id: str
    status: str = "RUNNING"
    entries: list[InitialImplementationEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "requirement_id": self.requirement_id,
            "status": self.status,
            "entries": [entry.to_dict() for entry in self.entries],
            "warnings": list(self.warnings),
        }


def classify_initial_targets(
    resolved_targets: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return owned business targets in deterministic backend/frontend order."""

    owned = [
        copy.deepcopy(row)
        for row in resolved_targets.get("owned_targets", [])
        if isinstance(row, dict) and str(row.get("module_id", "")).strip()
    ]
    backend = [
        row
        for row in owned
        if str(row.get("kind", "")).upper() in BACKEND_INITIAL_KINDS
    ]
    frontend = [
        row
        for row in owned
        if str(row.get("kind", "")).upper() in FRONTEND_INITIAL_KINDS
    ]
    ignored = [
        str(row.get("module_id", ""))
        for row in owned
        if str(row.get("kind", "")).upper()
        not in BACKEND_INITIAL_KINDS | FRONTEND_INITIAL_KINDS
    ]
    return [*_topological_backend_order(backend), *_frontend_order(frontend)], sorted(
        value for value in ignored if value
    )


def _topological_backend_order(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(row.get("module_id")): row for row in targets}
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[dict[str, Any]] = []

    def sort_key(module_id: str) -> tuple[int, str]:
        row = by_id[module_id]
        kind = str(row.get("kind", "")).upper()
        return ({"DB": 0, "FUNC": 1, "API": 2}.get(kind, 9), module_id)

    def visit(module_id: str) -> None:
        if module_id in visited:
            return
        if module_id in visiting:
            # The design stage owns cycle rejection. Keep planning total here:
            # a cycle is still emitted in stable order and recorded by the
            # caller rather than preventing unrelated targets from running.
            return
        visiting.add(module_id)
        callees = [
            str(value)
            for value in by_id[module_id].get("callees", [])
            if str(value) in by_id
        ]
        for callee in sorted(callees, key=sort_key):
            visit(callee)
        visiting.remove(module_id)
        visited.add(module_id)
        ordered.append(by_id[module_id])

    for module_id in sorted(by_id, key=sort_key):
        visit(module_id)
    return ordered


def _frontend_order(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {"STORE": 0, "COMPONENT": 1, "PAGE": 2, "LAYOUT": 3}
    return sorted(
        targets,
        key=lambda row: (
            priority.get(str(row.get("kind", "")).upper(), 9),
            str(row.get("module_id", "")),
        ),
    )


def ledger_entries_for_targets(
    targets: list[dict[str, Any]],
) -> list[InitialImplementationEntry]:
    return [
        InitialImplementationEntry(
            module_id=str(row.get("module_id", "")),
            kind=str(row.get("kind", "")).upper(),
            file=str(row.get("file", "")),
        )
        for row in targets
    ]


def finalize_ledger(ledger: InitialImplementationLedger) -> None:
    incomplete = [entry for entry in ledger.entries if entry.status == "INCOMPLETE"]
    pending = [entry for entry in ledger.entries if entry.status == "PENDING"]
    if pending:
        ledger.warnings.append(
            "INITIAL_IMPLEMENTATION_COVERAGE_INCOMPLETE: pending target(s) remained "
            f"for {ledger.requirement_id}: "
            f"{[entry.module_id for entry in pending]}."
        )
    if incomplete:
        ledger.status = "INCOMPLETE"
        ledger.warnings.append(
            "INITIAL_IMPLEMENTATION_WARNING: one or more owned business targets "
            f"did not reach a typecheck-passing implementation: "
            f"{[entry.module_id for entry in incomplete]}."
        )
    else:
        ledger.status = "COMPLETE"
    ledger.warnings = list(dict.fromkeys(ledger.warnings))
