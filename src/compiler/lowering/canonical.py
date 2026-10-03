from __future__ import annotations

import re

from ..ir.common import IRLevel, IRNode, NodeKind, Reference
from ..ir.design.module import DesignModule
from ..ir.implementation.file import FileIR
from ..ir.implementation.project import ImplementationIR
from ..ir.implementation.symbol import SymbolIR
from targets.react_fastapi import TargetProfile, load_target_profile


def _safe(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    return value or "generated"


def _kind(value: object) -> str:
    return str(value).split(".")[-1].lower()


def _path(node: IRNode, profile: TargetProfile) -> tuple[str, str]:
    kind = _kind(node.kind)
    name = _safe(node.name or node.id)
    if kind in {"entity", "field", "relation", "database_operation"}:
        return f"{profile.backend_root}/db/{name}{profile.backend_extension}", profile.backend_language
    if kind in {"api", "function"}:
        return f"{profile.backend_root}/{kind}/{name}{profile.backend_extension}", profile.backend_language
    if kind in {"page", "component", "ui", "event", "handler", "effect"}:
        return f"{profile.frontend_root}/{kind}/{name}{profile.frontend_extension}", profile.frontend_language
    return f"{profile.shared_root}/{name}.ts", profile.frontend_language


class CanonicalDesignLowering:
    """Lower canonical Design IR to a deterministic Implementation IR plan."""

    def __init__(self, profile: TargetProfile | None = None) -> None:
        self.profile = profile or load_target_profile()

    def create_implementation(self) -> ImplementationIR:
        return ImplementationIR(metadata={
            "source_level": str(IRLevel.DESIGN),
            "lowering": "canonical",
            "schema_version": 1,
            "target": self.profile.name,
        })

    def lower_nodes(
        self,
        design: DesignModule,
        implementation: ImplementationIR,
        kinds: set[str] | None = None,
    ) -> None:
        allowed = {str(kind).split(".")[-1].lower() for kind in kinds} if kinds else None
        for node in sorted(design.nodes.values(), key=lambda item: item.id):
            if allowed is not None and _kind(node.kind) not in allowed:
                continue
            path, language = _path(node, self.profile)
            file_id = f"file:{path}"
            symbol_id = f"symbol:{node.id}"
            implementation.files.setdefault(file_id, FileIR(
                id=file_id,
                name=path.rsplit("/", 1)[-1],
                path=path,
                language=language,
                requirement_ids=list(node.requirement_ids),
                attributes={"design_node_id": node.id},
            ))
            implementation.symbols[symbol_id] = SymbolIR(
                id=symbol_id,
                name=node.name or node.id,
                symbol_kind=_kind(node.kind),
                exported=True,
                requirement_ids=list(node.requirement_ids),
                attributes={"design_node_id": node.id, "file_id": file_id},
            )
            reference = Reference(symbol_id)
            if reference not in implementation.files[file_id].symbols:
                implementation.files[file_id].symbols.append(reference)

    def lower_edges(self, design: DesignModule, implementation: ImplementationIR) -> None:
        implementation.edges.extend(
            edge for edge in design.edges
            if edge.source_id in design.nodes and edge.target_id in design.nodes
            and edge not in implementation.edges
        )

    def lower(self, design: DesignModule) -> ImplementationIR:
        implementation = self.create_implementation()
        self.lower_nodes(design, implementation)
        self.lower_edges(design, implementation)
        return implementation


__all__ = ["CanonicalDesignLowering"]
