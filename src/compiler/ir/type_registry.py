"""Lazy named-contract queries; callers own validation and output ordering."""
from collections.abc import Iterable
from dataclasses import dataclass

from .type_contracts import TypeDefinition, iter_refs, refs


@dataclass(frozen=True)
class TypeResolution:
    """Reachable definitions, missing names and DFS cycle paths (not all cycles)."""
    names: frozenset[str]
    missing: frozenset[str]
    cycles: tuple[tuple[str, ...], ...]


class TypeRegistry:
    """Index by name, retaining the last definition and parsing only reached schemas."""

    def __init__(self, definitions: Iterable[TypeDefinition]) -> None:
        self._definitions = {row['name']: row for row in definitions}

    def __getitem__(self, name: str) -> TypeDefinition:
        return self._definitions[name]

    def resolve(self, contracts: Iterable[str], *, named_only: bool = False) -> TypeResolution:
        """Query without rejecting missing names/cycles; named_only ignores non-type refs."""
        if named_only:
            roots = (name for raw in contracts for name in iter_refs(raw, named_only=True))
        else:
            roots = set()
            for raw in contracts:
                roots.update(refs(raw))
            roots = iter(sorted(roots))
        names, missing, cycles = set(), set(), set()
        active, path = {}, []
        pending = [(None, iter(roots))]
        while pending:
            parent, references = pending[-1]
            try:
                name = next(references)
            except StopIteration:
                pending.pop()
                if parent is not None:
                    active.pop(parent)
                    path.pop()
                continue
            if name in active:
                cycles.add(tuple(path[active[name]:] + [name]))
                continue
            if name in names or name in missing:
                continue
            if name not in self._definitions:
                missing.add(name)
                continue
            raw = self._definitions[name]['schema']
            dependencies = (iter_refs(raw, named_only=True) if named_only else iter(sorted(refs(raw))))
            names.add(name)
            active[name] = len(path)
            path.append(name)
            pending.append((name, dependencies))
        return TypeResolution(frozenset(names), frozenset(missing), tuple(sorted(cycles)))
